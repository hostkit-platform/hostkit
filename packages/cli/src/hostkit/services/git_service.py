"""Git service for HostKit deployments from Git repositories."""

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class GitConfig:
    """Git configuration for a project."""

    project_name: str
    repo_url: str
    default_branch: str
    ssh_key_path: str | None
    created_at: str
    updated_at: str


@dataclass
class GitInfo:
    """Git information for a cloned repository."""

    commit: str
    branch: str | None
    tag: str | None
    commit_message: str
    commit_author: str
    commit_date: str


class GitServiceError(Exception):
    """Base exception for git service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class GitService:
    """Handle Git operations for project deployments."""

    # Valid URL patterns
    HTTPS_PATTERN = re.compile(
        r"^https://[a-zA-Z0-9.-]+/.*\.git$|^https://[a-zA-Z0-9.-]+/[^/]+/[^/]+/?$"
    )
    SSH_PATTERN = re.compile(
        r"^git@[a-zA-Z0-9.-]+:[a-zA-Z0-9._/-]+\.git$|^git@[a-zA-Z0-9.-]+:[a-zA-Z0-9._/-]+/?$"
    )

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.cache_dir = Path("/var/lib/hostkit/git-cache")

    def _validate_url(self, url: str) -> None:
        """Validate that URL is a safe git URL."""
        url = url.strip()
        if not (self.HTTPS_PATTERN.match(url) or self.SSH_PATTERN.match(url)):
            raise GitServiceError(
                code="INVALID_GIT_URL",
                message=f"Invalid git URL: {url}",
                suggestion=(
                    "Use https:// or git@ URL format"
                    " (e.g., https://github.com/user/repo.git"
                    " or git@github.com:user/repo.git)"
                ),
            )

    def _ensure_git_installed(self) -> None:
        """Ensure git is installed on the system."""
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise GitServiceError(
                code="GIT_NOT_INSTALLED",
                message="Git is not installed on this system",
                suggestion="Install git with: apt-get install git",
            )

    def _get_cache_path(self, project: str) -> Path:
        """Get the cache path for a project's bare clone."""
        return self.cache_dir / project

    def _run_git(
        self,
        args: list[str],
        cwd: Path | str | None = None,
        ssh_key: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a git command with optional SSH key."""
        cmd = ["git"] + args
        env = None

        if ssh_key:
            # Use GIT_SSH_COMMAND to specify the SSH key
            env = {
                "GIT_SSH_COMMAND": f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new",
            }

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                check=True,
                env=env,
                timeout=300,  # 5 minute timeout for clone/fetch
            )
            return result
        except subprocess.TimeoutExpired:
            raise GitServiceError(
                code="GIT_TIMEOUT",
                message="Git operation timed out after 5 minutes",
                suggestion="Check network connectivity and repository size",
            )
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            raise GitServiceError(
                code="GIT_COMMAND_FAILED",
                message=f"Git command failed: {error_msg}",
                suggestion="Check repository URL and access permissions",
            )

    def clone_to_directory(
        self,
        repo_url: str,
        target_dir: Path,
        branch: str | None = None,
        tag: str | None = None,
        commit: str | None = None,
        ssh_key: str | None = None,
        project: str | None = None,
    ) -> GitInfo:
        """
        Clone a repository to a target directory.

        Uses a cached bare clone for faster subsequent clones.

        Args:
            repo_url: Git repository URL
            target_dir: Directory to clone into
            branch: Branch to checkout (default: repo default)
            tag: Tag to checkout (overrides branch)
            commit: Specific commit to checkout (overrides branch/tag)
            ssh_key: Path to SSH private key for auth
            project: Project name (for caching)

        Returns:
            GitInfo with commit details
        """
        self._ensure_git_installed()
        self._validate_url(repo_url)

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Use project-specific cache or temp cache
        cache_path = self._get_cache_path(project) if project else None

        # Clone or update cache
        if cache_path and cache_path.exists():
            # Update existing cache
            self._run_git(["fetch", "--all", "--prune"], cwd=cache_path, ssh_key=ssh_key)
        elif cache_path:
            # Create new bare cache
            self._run_git(
                ["clone", "--bare", repo_url, str(cache_path)],
                ssh_key=ssh_key,
            )

        # Clone from cache (or directly if no project)
        source = str(cache_path) if cache_path else repo_url
        clone_args = ["clone"]

        if branch and not tag and not commit:
            clone_args.extend(["--branch", branch])

        clone_args.extend([source, str(target_dir)])

        self._run_git(clone_args, ssh_key=ssh_key if not cache_path else None)

        # Checkout specific ref if needed
        if tag:
            self._run_git(["checkout", f"tags/{tag}"], cwd=target_dir)
        elif commit:
            self._run_git(["checkout", commit], cwd=target_dir)
        elif branch:
            # Already checked out by clone --branch, but ensure we're on it
            self._run_git(["checkout", branch], cwd=target_dir)

        # Get commit info
        return self.get_info(target_dir)

    def get_info(self, repo_dir: Path) -> GitInfo:
        """Get git information from a repository directory."""
        # Get current commit
        result = self._run_git(["rev-parse", "HEAD"], cwd=repo_dir)
        commit = result.stdout.strip()

        # Get branch name (may be None if detached HEAD)
        try:
            result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
            branch = result.stdout.strip()
            if branch == "HEAD":
                branch = None
        except GitServiceError:
            branch = None

        # Try to get tag
        tag = None
        try:
            result = self._run_git(["describe", "--tags", "--exact-match"], cwd=repo_dir)
            tag = result.stdout.strip()
        except GitServiceError:
            pass  # No tag at current commit

        # Get commit message
        result = self._run_git(["log", "-1", "--format=%s"], cwd=repo_dir)
        commit_message = result.stdout.strip()

        # Get commit author
        result = self._run_git(["log", "-1", "--format=%an <%ae>"], cwd=repo_dir)
        commit_author = result.stdout.strip()

        # Get commit date
        result = self._run_git(["log", "-1", "--format=%aI"], cwd=repo_dir)
        commit_date = result.stdout.strip()

        return GitInfo(
            commit=commit,
            branch=branch,
            tag=tag,
            commit_message=commit_message,
            commit_author=commit_author,
            commit_date=commit_date,
        )

    def configure_project(
        self,
        project: str,
        repo_url: str,
        default_branch: str = "main",
        ssh_key_path: str | None = None,
    ) -> GitConfig:
        """
        Configure git settings for a project.

        Args:
            project: Project name
            repo_url: Git repository URL
            default_branch: Default branch to deploy
            ssh_key_path: Path to SSH key for private repos
        """
        self._validate_url(repo_url)

        # Verify project exists
        project_info = self.db.get_project(project)
        if not project_info:
            raise GitServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' not found",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        now = datetime.utcnow().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO git_config
                (project_name, repo_url, default_branch,
                ssh_key_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_name) DO UPDATE SET
                    repo_url = excluded.repo_url,
                    default_branch = excluded.default_branch,
                    ssh_key_path = excluded.ssh_key_path,
                    updated_at = excluded.updated_at
                """,
                (project, repo_url, default_branch, ssh_key_path, now, now),
            )

        return GitConfig(
            project_name=project,
            repo_url=repo_url,
            default_branch=default_branch,
            ssh_key_path=ssh_key_path,
            created_at=now,
            updated_at=now,
        )

    def get_project_config(self, project: str) -> GitConfig | None:
        """Get git configuration for a project."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM git_config WHERE project_name = ?",
                (project,),
            )
            row = cursor.fetchone()
            if row:
                return GitConfig(
                    project_name=row["project_name"],
                    repo_url=row["repo_url"],
                    default_branch=row["default_branch"],
                    ssh_key_path=row["ssh_key_path"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
        return None

    def clear_project_config(self, project: str) -> bool:
        """Clear git configuration for a project."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM git_config WHERE project_name = ?",
                (project,),
            )
            return cursor.rowcount > 0

    def clear_cache(self, project: str) -> bool:
        """Clear the cached bare clone for a project."""
        cache_path = self._get_cache_path(project)
        if cache_path.exists():
            shutil.rmtree(cache_path)
            return True
        return False

    def list_cached_repos(self) -> list[dict[str, Any]]:
        """List all cached git repositories."""
        result = []
        if self.cache_dir.exists():
            for path in self.cache_dir.iterdir():
                if path.is_dir():
                    # Get size
                    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    result.append(
                        {
                            "project": path.name,
                            "path": str(path),
                            "size_bytes": size,
                        }
                    )
        return result
