"""Release management service for HostKit.

Handles release-based deployments with timestamped release directories
and symlink switching for instant rollbacks.
"""

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.database import get_db

# Default number of releases to retain
DEFAULT_RELEASE_RETENTION = 5


@dataclass
class Release:
    """Represents a deployment release."""

    id: str
    project: str
    release_name: str
    release_path: str
    deployed_at: str
    is_current: bool
    files_synced: int | None
    deployed_by: str | None
    checkpoint_id: int | None = None
    env_snapshot: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_tag: str | None = None
    git_repo: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Release":
        """Create a Release from a database dict."""
        return cls(
            id=data["id"],
            project=data["project"],
            release_name=data["release_name"],
            release_path=data["release_path"],
            deployed_at=data["deployed_at"],
            is_current=bool(data["is_current"]),
            files_synced=data.get("files_synced"),
            deployed_by=data.get("deployed_by"),
            checkpoint_id=data.get("checkpoint_id"),
            env_snapshot=data.get("env_snapshot"),
            git_commit=data.get("git_commit"),
            git_branch=data.get("git_branch"),
            git_tag=data.get("git_tag"),
            git_repo=data.get("git_repo"),
        )


class ReleaseServiceError(Exception):
    """Exception for release service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class ReleaseService:
    """Service for managing release-based deployments.

    Directory structure after migration:
        /home/{project}/
        ├── releases/
        │   ├── 20251213-143022/     # Release 1
        │   └── 20251213-161032/     # Release 2 (current)
        ├── app -> releases/20251213-161032/  # Symlink to current
        ├── shared/                   # Persistent data
        ├── venv/ or node_modules/    # Runtime dependencies
        └── .env
    """

    def __init__(self) -> None:
        self.db = get_db()

    def _releases_dir(self, project: str) -> Path:
        """Get the releases directory for a project."""
        return Path(f"/home/{project}/releases")

    def _app_symlink(self, project: str) -> Path:
        """Get the app symlink path for a project."""
        return Path(f"/home/{project}/app")

    def _shared_dir(self, project: str) -> Path:
        """Get the shared directory for persistent data."""
        return Path(f"/home/{project}/shared")

    def _generate_release_name(self) -> str:
        """Generate a timestamped release name."""
        return datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    def _get_retention(self) -> int:
        """Get the number of releases to retain."""
        config_value = self.db.get_config("release_retention")
        if config_value:
            try:
                return int(config_value)
            except ValueError:
                pass
        return DEFAULT_RELEASE_RETENTION

    def _validate_project(self, project: str) -> None:
        """Validate that the project exists."""
        if not self.db.get_project(project):
            raise ReleaseServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def _chown_recursive(self, path: Path, user: str) -> None:
        """Change ownership of a path recursively."""
        try:
            subprocess.run(
                ["chown", "-R", f"{user}:{user}", str(path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # Best effort

    def is_release_based(self, project: str) -> bool:
        """Check if a project uses release-based deployments.

        A project is release-based if:
        1. The releases directory exists AND has releases, OR
        2. The app path is a symlink (pointing to a release)

        Returns:
            True if the project uses releases, False if it's legacy
        """
        self._validate_project(project)

        releases_dir = self._releases_dir(project)
        app_path = self._app_symlink(project)

        # Check if app is a symlink
        if app_path.is_symlink():
            return True

        # Check if releases dir exists and has subdirectories
        if releases_dir.exists() and any(releases_dir.iterdir()):
            return True

        return False

    def migrate_to_releases(self, project: str) -> Release:
        """Migrate a legacy project to release-based deployment.

        Takes the existing /home/{project}/app directory contents and
        moves them into the first release.

        Args:
            project: Project name

        Returns:
            The created initial release
        """
        self._validate_project(project)

        app_path = self._app_symlink(project)
        releases_dir = self._releases_dir(project)
        shared_dir = self._shared_dir(project)

        # Already migrated
        if app_path.is_symlink():
            current = self.get_current_release(project)
            if current:
                return current
            raise ReleaseServiceError(
                code="INVALID_STATE",
                message="App is a symlink but no current release found",
                suggestion="Check the releases directory manually",
            )

        # Create releases and shared directories
        releases_dir.mkdir(parents=True, exist_ok=True)
        shared_dir.mkdir(parents=True, exist_ok=True)

        # Generate release name and path
        release_name = self._generate_release_name()
        release_path = releases_dir / release_name

        # Move existing app to release directory
        if app_path.exists() and app_path.is_dir():
            # Rename app to the release directory
            app_path.rename(release_path)
        else:
            # No existing app - create empty release directory
            release_path.mkdir(parents=True, exist_ok=True)

        # Create symlink from app to release
        app_path.symlink_to(release_path)

        # Set ownership
        self._chown_recursive(releases_dir, project)
        self._chown_recursive(shared_dir, project)

        # Also fix the symlink ownership
        try:
            subprocess.run(
                ["chown", "-h", f"{project}:{project}", str(app_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

        # Count files in release
        file_count = sum(1 for _ in release_path.rglob("*") if _.is_file())

        # Record in database
        release_id = str(uuid.uuid4())
        deployed_by = os.environ.get("USER", "root")

        release_data = self.db.create_release(
            release_id=release_id,
            project=project,
            release_name=release_name,
            release_path=str(release_path),
            is_current=True,
            files_synced=file_count,
            deployed_by=deployed_by,
        )

        return Release.from_dict(release_data)

    def create_release(
        self,
        project: str,
        files_synced: int | None = None,
        deployed_by: str | None = None,
    ) -> Release:
        """Create a new release directory for deployment.

        Does NOT activate the release - call activate_release() after
        syncing files to make it current.

        Args:
            project: Project name
            files_synced: Number of files synced (optional, update later)
            deployed_by: Username performing the deployment

        Returns:
            The created release
        """
        self._validate_project(project)

        releases_dir = self._releases_dir(project)
        shared_dir = self._shared_dir(project)

        # Ensure directories exist
        releases_dir.mkdir(parents=True, exist_ok=True)
        shared_dir.mkdir(parents=True, exist_ok=True)

        # Generate release
        release_name = self._generate_release_name()
        release_path = releases_dir / release_name
        release_path.mkdir(parents=True, exist_ok=True)

        # Set ownership
        self._chown_recursive(release_path, project)

        # Record in database (not current yet)
        release_id = str(uuid.uuid4())
        if deployed_by is None:
            deployed_by = os.environ.get("USER", "root")

        release_data = self.db.create_release(
            release_id=release_id,
            project=project,
            release_name=release_name,
            release_path=str(release_path),
            is_current=False,
            files_synced=files_synced,
            deployed_by=deployed_by,
        )

        return Release.from_dict(release_data)

    def activate_release(self, project: str, release_name: str) -> Release:
        """Activate a release by updating the app symlink.

        This is an atomic operation - the symlink is replaced in a single
        rename operation, ensuring zero-downtime switching.

        Args:
            project: Project name
            release_name: Name of the release to activate

        Returns:
            The activated release
        """
        self._validate_project(project)

        release = self.db.get_release(project, release_name)
        if not release:
            raise ReleaseServiceError(
                code="RELEASE_NOT_FOUND",
                message=f"Release '{release_name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit rollback {project} --list' to see available releases",
            )

        release_path = Path(release["release_path"])
        if not release_path.exists():
            raise ReleaseServiceError(
                code="RELEASE_PATH_MISSING",
                message=f"Release directory does not exist: {release_path}",
                suggestion="The release may have been manually deleted",
            )

        app_path = self._app_symlink(project)

        # Create temporary symlink and atomically replace
        tmp_link = app_path.parent / f".app_tmp_{uuid.uuid4().hex[:8]}"
        try:
            tmp_link.symlink_to(release_path)

            # Atomic replace
            tmp_link.rename(app_path)

            # Fix symlink ownership
            subprocess.run(
                ["chown", "-h", f"{project}:{project}", str(app_path)],
                check=True,
                capture_output=True,
            )
        except Exception as e:
            # Clean up temp link if it exists
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            raise ReleaseServiceError(
                code="ACTIVATE_FAILED",
                message=f"Failed to activate release: {e}",
                suggestion="Check directory permissions",
            )

        # Update database
        self.db.set_current_release(project, release_name)

        return Release.from_dict(self.db.get_release(project, release_name))  # type: ignore

    def get_current_release(self, project: str) -> Release | None:
        """Get the current active release for a project.

        Args:
            project: Project name

        Returns:
            The current release, or None if not release-based
        """
        self._validate_project(project)

        release = self.db.get_current_release(project)
        if release:
            return Release.from_dict(release)
        return None

    def get_release(self, project: str, release_name: str) -> Release:
        """Get a specific release.

        Args:
            project: Project name
            release_name: Release name (timestamp)

        Returns:
            The release
        """
        self._validate_project(project)

        release = self.db.get_release(project, release_name)
        if not release:
            raise ReleaseServiceError(
                code="RELEASE_NOT_FOUND",
                message=f"Release '{release_name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit rollback {project} --list' to see available releases",
            )

        return Release.from_dict(release)

    def list_releases(self, project: str, limit: int = 10) -> list[Release]:
        """List releases for a project, most recent first.

        Args:
            project: Project name
            limit: Maximum number of releases to return

        Returns:
            List of releases
        """
        self._validate_project(project)

        releases = self.db.list_releases(project, limit=limit)
        return [Release.from_dict(r) for r in releases]

    def get_previous_release(self, project: str) -> Release | None:
        """Get the release before the current one.

        Args:
            project: Project name

        Returns:
            The previous release, or None if there isn't one
        """
        self._validate_project(project)

        releases = self.db.list_releases(project, limit=2)
        if len(releases) < 2:
            return None

        # releases[0] is current, releases[1] is previous
        return Release.from_dict(releases[1])

    def cleanup_old_releases(self, project: str) -> int:
        """Remove old releases beyond the retention limit.

        Keeps the configured number of most recent releases and removes
        older ones. Never removes the current release.

        Args:
            project: Project name

        Returns:
            Number of releases removed
        """
        self._validate_project(project)

        retention = self._get_retention()
        releases = self.db.list_releases(project, limit=100)  # Get all

        if len(releases) <= retention:
            return 0

        # Get releases to remove (keep the most recent 'retention' count)
        to_remove = releases[retention:]
        removed = 0

        for release_data in to_remove:
            # Never remove current release
            if release_data["is_current"]:
                continue

            release_path = Path(release_data["release_path"])

            # Delete directory
            if release_path.exists():
                try:
                    shutil.rmtree(release_path)
                except OSError:
                    continue  # Skip if can't delete

            # Delete from database
            if self.db.delete_release(release_data["id"]):
                removed += 1

        return removed

    def update_release_files(self, project: str, release_name: str, files_synced: int) -> bool:
        """Update the file count for a release.

        Args:
            project: Project name
            release_name: Release name
            files_synced: Number of files synced

        Returns:
            True if updated successfully
        """
        release = self.db.get_release(project, release_name)
        if not release:
            return False
        return self.db.update_release_files(release["id"], files_synced)

    def update_release_snapshot(
        self,
        project: str,
        release_name: str,
        checkpoint_id: int | None = None,
        env_snapshot: str | None = None,
    ) -> bool:
        """Update the checkpoint and/or env snapshot for a release.

        Args:
            project: Project name
            release_name: Release name
            checkpoint_id: Database checkpoint ID for this release
            env_snapshot: JSON string of env vars at deploy time

        Returns:
            True if updated successfully
        """
        release = self.db.get_release(project, release_name)
        if not release:
            return False
        return self.db.update_release_snapshot(
            release["id"],
            checkpoint_id=checkpoint_id,
            env_snapshot=env_snapshot,
        )

    def update_release_git_info(
        self,
        project: str,
        release_name: str,
        git_commit: str | None = None,
        git_branch: str | None = None,
        git_tag: str | None = None,
        git_repo: str | None = None,
    ) -> bool:
        """Update the git information for a release.

        Args:
            project: Project name
            release_name: Release name
            git_commit: Git commit hash
            git_branch: Git branch name
            git_tag: Git tag (if deployed from tag)
            git_repo: Git repository URL

        Returns:
            True if updated successfully
        """
        release = self.db.get_release(project, release_name)
        if not release:
            return False
        return self.db.update_release_git_info(
            release["id"],
            git_commit=git_commit,
            git_branch=git_branch,
            git_tag=git_tag,
            git_repo=git_repo,
        )
