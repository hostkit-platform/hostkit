"""Next.js standalone deployment handler for HostKit."""

import shutil
import subprocess
from pathlib import Path

from hostkit.services.build_detector import BuildDetector, BuildType


class NextJSHandlerError(Exception):
    """Error during Next.js deployment."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class NextJSHandler:
    """
    Handle Next.js standalone build deployment.

    Next.js standalone builds have a complex structure:
    - The build embeds the full source path (e.g., .next/standalone/Users/dev/project/)
    - server.js is at the embedded path root
    - Static files must be copied to .next/static relative to server.js
    - Public files go to ./public relative to server.js

    This handler normalizes the structure for deployment.
    """

    def __init__(self):
        self.build_detector = BuildDetector()

    def deploy_standalone(
        self, source_path: Path, release_path: Path, project: str
    ) -> int:
        """
        Deploy Next.js standalone build to release directory.

        This replicates the logic from the dashboard's deploy.sh:
        1. Find standalone root (contains server.js)
        2. Copy standalone root contents to release
        3. Copy static files to .next/static
        4. Copy public directory if exists

        Args:
            source_path: Source directory containing the build
            release_path: Target release directory
            project: Project name for ownership

        Returns:
            Number of files synced (approximate)

        Raises:
            NextJSHandlerError: If deployment fails
        """
        # Detect and validate
        result = self.build_detector.detect(source_path)
        if result.build_type != BuildType.NEXTJS_STANDALONE:
            raise NextJSHandlerError(
                code="NOT_NEXTJS_STANDALONE",
                message="Source is not a Next.js standalone build",
                suggestion="Run 'npm run build' with standalone output enabled",
            )

        if not result.standalone_root:
            raise NextJSHandlerError(
                code="NEXTJS_SERVER_NOT_FOUND",
                message="Could not find server.js in standalone build",
                suggestion="Ensure your Next.js build completed successfully",
            )

        standalone_root = result.standalone_root
        files_synced = 0

        # Step 1: Copy standalone root to release directory
        # This contains server.js, .next/server, node_modules, etc.
        files_synced += self._copy_directory(standalone_root, release_path)

        # Step 2: Copy static files to .next/static
        # Static files are at source_path/.next/static, not in standalone
        static_src = source_path / ".next" / "static"
        if static_src.exists():
            static_dest = release_path / ".next" / "static"
            static_dest.mkdir(parents=True, exist_ok=True)
            files_synced += self._copy_directory(static_src, static_dest)

        # Step 3: Copy public directory if exists
        public_src = source_path / "public"
        if public_src.exists():
            public_dest = release_path / "public"
            public_dest.mkdir(parents=True, exist_ok=True)
            files_synced += self._copy_directory(public_src, public_dest)

        # Fix ownership
        self._fix_ownership(release_path, project)

        return files_synced

    def _copy_directory(self, src: Path, dest: Path) -> int:
        """
        Copy directory contents using rsync for efficiency.

        Args:
            src: Source directory
            dest: Destination directory

        Returns:
            Approximate number of files copied
        """
        # Ensure destination exists
        dest.mkdir(parents=True, exist_ok=True)

        # Use rsync for efficient copying with --delete to mirror source
        cmd = [
            "rsync",
            "-av",
            "--delete",
            f"{src}/",
            f"{dest}/",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            raise NextJSHandlerError(
                code="RSYNC_FAILED",
                message=f"Failed to copy files: {e.stderr}",
                suggestion="Check that source directory is readable",
            )

        # Count files from rsync output
        lines = result.stdout.strip().split("\n")
        file_count = len(
            [
                line
                for line in lines
                if line
                and not line.startswith("sending")
                and not line.startswith("sent")
                and not line.startswith("total")
                and line.strip()
            ]
        )

        return file_count

    def _fix_ownership(self, path: Path, project: str) -> None:
        """Fix ownership of deployed files."""
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # Non-fatal - files were synced but ownership may be wrong
            pass
