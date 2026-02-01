"""Build type detection service for HostKit deployments."""

from enum import Enum
from pathlib import Path
from typing import NamedTuple


class BuildType(Enum):
    """Detected build types for deployment."""

    PYTHON = "python"
    NODE = "node"
    NEXTJS_STANDARD = "nextjs_standard"
    NEXTJS_STANDALONE = "nextjs_standalone"
    STATIC = "static"
    UNKNOWN = "unknown"


class DetectionResult(NamedTuple):
    """Result of build type detection."""

    build_type: BuildType
    server_js_path: Path | None = None  # For Next.js standalone
    standalone_root: Path | None = None  # Root dir containing server.js
    warning: str | None = None  # Warning message (e.g., missing node_modules)


class BuildDetector:
    """Detect the build type of a source directory."""

    def detect(self, source_path: Path) -> DetectionResult:
        """
        Detect build type from source directory structure.

        Priority order:
        1. Next.js standalone flattened (server.js at root + .next/server)
        2. Next.js standalone nested (has .next/standalone with server.js)
        3. Next.js standard (has .next but no standalone)
        4. Node.js (has package.json)
        5. Python (has requirements.txt or pyproject.toml)
        6. Static (has index.html)
        7. Unknown

        Args:
            source_path: Path to the source directory

        Returns:
            DetectionResult with build type and optional paths
        """
        # Check for flattened standalone first (server.js at root with .next/server)
        # This happens when deploying the CONTENTS of .next/standalone/
        server_js_root = source_path / "server.js"
        next_server_dir = source_path / ".next" / "server"
        if server_js_root.exists() and next_server_dir.exists():
            # This is a flattened standalone deployment
            warning = None
            node_modules = source_path / "node_modules"
            if not node_modules.exists():
                warning = (
                    "Standalone node_modules missing. "
                    "Ensure node_modules is included when deploying standalone."
                )
            return DetectionResult(
                build_type=BuildType.NEXTJS_STANDALONE,
                server_js_path=server_js_root,
                standalone_root=source_path,
                warning=warning,
            )

        # Check for nested standalone (.next/standalone/server.js)
        next_dir = source_path / ".next"
        if next_dir.exists():
            standalone_dir = next_dir / "standalone"
            if standalone_dir.exists():
                server_js = self._find_server_js(standalone_dir)
                if server_js:
                    standalone_root = server_js.parent
                    # Check for node_modules in standalone root
                    warning = None
                    node_modules = standalone_root / "node_modules"
                    if not node_modules.exists():
                        warning = (
                            "Standalone node_modules missing. "
                            "Ensure .next/standalone/.../node_modules is included in source."
                        )
                    return DetectionResult(
                        build_type=BuildType.NEXTJS_STANDALONE,
                        server_js_path=server_js,
                        standalone_root=standalone_root,
                        warning=warning,
                    )
            # Has .next but no standalone server.js
            return DetectionResult(build_type=BuildType.NEXTJS_STANDARD)

        # Check for Node.js (package.json)
        if (source_path / "package.json").exists():
            return DetectionResult(build_type=BuildType.NODE)

        # Check for Python
        if (source_path / "requirements.txt").exists() or (
            source_path / "pyproject.toml"
        ).exists():
            return DetectionResult(build_type=BuildType.PYTHON)

        # Check for static site
        if (source_path / "index.html").exists():
            return DetectionResult(build_type=BuildType.STATIC)

        return DetectionResult(build_type=BuildType.UNKNOWN)

    def _find_server_js(self, standalone_dir: Path) -> Path | None:
        """
        Find server.js in standalone directory.

        Next.js standalone embeds the full source path when building,
        so server.js may be deeply nested like:
        .next/standalone/Users/dev/project/dashboard/server.js

        This method searches for server.js recursively, excluding node_modules.

        Args:
            standalone_dir: The .next/standalone directory

        Returns:
            Path to server.js if found, None otherwise
        """
        # Look for server.js files, excluding node_modules
        for server_js in standalone_dir.rglob("server.js"):
            # Skip node_modules
            if "node_modules" in server_js.parts:
                continue

            # Verify this looks like a Next.js server by checking for
            # sibling .next directory (Next.js standalone structure)
            parent = server_js.parent
            next_subdir = parent / ".next"
            if next_subdir.exists() and next_subdir.is_dir():
                return server_js

        # If we didn't find one with .next sibling, try any server.js
        # that's not in node_modules (less strict check)
        for server_js in standalone_dir.rglob("server.js"):
            if "node_modules" not in server_js.parts:
                return server_js

        return None
