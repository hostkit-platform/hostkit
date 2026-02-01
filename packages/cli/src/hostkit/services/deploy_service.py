"""Deployment service for HostKit projects."""

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.services.build_detector import BuildDetector, BuildType
from hostkit.services.release_service import Release, ReleaseService

if TYPE_CHECKING:
    from hostkit.services.git_service import GitInfo


@dataclass
class DeployResult:
    """Result of a deployment operation."""

    project: str
    files_synced: int
    dependencies_installed: bool
    secrets_injected: bool
    secrets_count: int
    service_restarted: bool
    runtime: str
    release: Release | None = field(default=None)
    checkpoint_id: int | None = field(default=None)
    env_snapshot_captured: bool = field(default=False)
    override_used: bool = field(default=False)
    git_info: "GitInfo | None" = field(default=None)
    build_type: str | None = field(default=None)
    validation_passed: bool = field(default=True)
    validation_message: str | None = field(default=None)
    app_built: bool = field(default=False)
    iron_session_installed: bool = field(default=False)


class DeployServiceError(Exception):
    """Base exception for deploy service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class DeployValidationError(DeployServiceError):
    """Error during post-deploy validation."""

    pass


class DeployService:
    """Handle project deployments using release-based system."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.release_service = ReleaseService()
        self.build_detector = BuildDetector()

    def deploy(
        self,
        project: str,
        source: Path,
        build_app: bool = False,
        install_deps: bool = False,
        inject_secrets: bool = False,
        restart: bool = True,
        override_ratelimit: bool = False,
    ) -> DeployResult:
        """
        Deploy code to a project using release-based deployment.

        The deployment process:
        0. Check rate limit (unless override)
        1. Migrate to releases if needed (first deploy)
        2. Build app if requested (npm install && npm run build)
        3. Capture env snapshot (for rollback)
        4. Create database checkpoint if project has a database (for rollback)
        5. Create a new release directory
        6. Rsync files to the release directory
        7. Install dependencies if requested
        8. Activate the release (atomic symlink swap)
        9. Link checkpoint and env snapshot to release
        10. Inject secrets if requested
        11. Restart service if requested
        12. Cleanup old releases
        13. Record deploy in history

        Args:
            project: Project name
            source: Local source directory to sync
            build_app: Whether to build the app before deploying
            install_deps: Whether to install dependencies
            inject_secrets: Whether to inject secrets from vault into .env
            restart: Whether to restart the service after deploy
            override_ratelimit: Whether to bypass rate limit checks

        Returns:
            DeployResult with deployment details including release info
        """
        start_time = time.time()

        # Get project info
        project_info = self.db.get_project(project)
        if not project_info:
            raise DeployServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' not found",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # Step 0a: Check if project is paused (auto-pause)
        from hostkit.services.auto_pause_service import AutoPauseError, AutoPauseService

        auto_pause_service = AutoPauseService()

        try:
            auto_pause_service.check_before_deploy(project)
        except AutoPauseError as e:
            raise DeployServiceError(
                code=e.code,
                message=e.message,
                suggestion=e.suggestion,
            )

        # Step 0b: Check rate limit (unless override)
        from hostkit.services.rate_limit_service import RateLimitError, RateLimitService

        rate_limit_service = RateLimitService()

        if not override_ratelimit:
            try:
                rate_limit_service.check_rate_limit(project)
            except RateLimitError as e:
                # Record the blocked attempt
                rate_limit_service.record_deploy(
                    project_name=project,
                    success=False,
                    source_type="rsync",
                    error_message=e.message,
                )
                raise DeployServiceError(
                    code=e.code,
                    message=e.message,
                    suggestion=e.suggestion,
                )

        runtime = project_info.get("runtime", "python")

        # Ensure source exists
        if not source.exists():
            # Check if this looks like a local workstation path
            source_str = str(source)
            looks_like_local = (
                source_str.startswith("/Users/")  # macOS
                or source_str.startswith("/home/")
                and not source_str.startswith(
                    "/home/" + project
                )  # Linux home (but not project dir)
                or source_str.startswith("C:\\")  # Windows
                or source_str.startswith("./")
                and "/" in source_str[2:]  # Relative with nested dirs
            )

            if looks_like_local:
                raise DeployServiceError(
                    code="SOURCE_NOT_FOUND",
                    message=f"Source path '{source}' not found on VPS",
                    suggestion=(
                        "The --source flag expects a path on the VPS, not your local machine.\n\n"
                        "To deploy from your local machine:\n"
                        "  1. Use the hostkit_deploy_local MCP tool, or\n"
                        "  2. Manually rsync then deploy:\n"
                        f"       rsync -avz ./your-app ai-operator@vps:/tmp/{project}-deploy/\n"
                        f"       hostkit deploy {project} --source /tmp/{project}-deploy"
                    ),
                )
            else:
                raise DeployServiceError(
                    code="SOURCE_NOT_FOUND",
                    message=f"Source directory '{source}' does not exist on VPS",
                    suggestion=(
                        f"Check the path exists: ls -la {source}\n"
                        "Or deploy from your local machine using hostkit_deploy_local MCP tool"
                    ),
                )

        if not source.is_dir():
            raise DeployServiceError(
                code="SOURCE_NOT_DIRECTORY",
                message=f"Source path '{source}' is not a directory",
                suggestion="Provide a directory path, not a file",
            )

        # Step 1: Migrate to releases if this is a legacy project
        if not self.release_service.is_release_based(project):
            self.release_service.migrate_to_releases(project)

        # Step 2: Build app if requested
        # This syncs source to a temp dir on VPS, builds there, then uses built output
        app_built = False
        build_temp_dir = None
        if build_app:
            build_temp_dir, source = self._build_app(project, source, runtime)
            app_built = True

        # Wrap remaining steps in try/finally to ensure build temp dir cleanup
        try:
            return self._deploy_inner(
                project=project,
                source=source,
                runtime=runtime,
                install_deps=install_deps,
                inject_secrets=inject_secrets,
                restart=restart,
                override_ratelimit=override_ratelimit,
                start_time=start_time,
                rate_limit_service=rate_limit_service,
                app_built=app_built,
            )
        finally:
            # Cleanup build temp dir if we created one
            if build_temp_dir and Path(build_temp_dir).exists():
                try:
                    shutil.rmtree(build_temp_dir)
                except Exception:
                    pass  # Non-fatal cleanup failure

    def _deploy_inner(
        self,
        project: str,
        source: Path,
        runtime: str,
        install_deps: bool,
        inject_secrets: bool,
        restart: bool,
        override_ratelimit: bool,
        start_time: float,
        rate_limit_service,
        app_built: bool = False,
    ) -> DeployResult:
        """Inner deployment logic after optional build step."""

        # Step 3: Capture env snapshot before deploy (for rollback)
        env_snapshot = None
        env_snapshot_captured = False
        try:
            from hostkit.services.env_service import EnvService

            env_service = EnvService()
            env_snapshot = env_service.capture_snapshot(project)
            env_snapshot_captured = True
        except Exception:
            pass  # Non-fatal - env snapshot is optional

        # Step 3: Create database checkpoint if project has a database
        checkpoint_id = None
        if self._project_has_database(project):
            try:
                from hostkit.services.checkpoint_service import CheckpointService

                checkpoint_service = CheckpointService()
                checkpoint = checkpoint_service.create_checkpoint(
                    project_name=project,
                    label="pre-deploy",
                    checkpoint_type="pre_deploy",
                    trigger_source="deploy",
                )
                checkpoint_id = checkpoint.id
            except Exception:
                pass  # Non-fatal - checkpoint is optional

        # Step 4: Detect build type
        detection = self.build_detector.detect(source)
        build_type = detection.build_type

        # Check for critical warnings (e.g., missing standalone node_modules)
        if detection.warning and build_type == BuildType.NEXTJS_STANDALONE:
            raise DeployServiceError(
                code="NEXTJS_STANDALONE_INCOMPLETE",
                message=detection.warning,
                suggestion=(
                    "Next.js standalone builds require node_modules in the standalone directory. "
                    "Make sure you're not excluding it when syncing the source."
                ),
            )

        # Step 5: Create new release directory
        release = self.release_service.create_release(project)
        release_path = Path(release.release_path)

        # Step 6: Sync files to release directory based on build type
        if build_type == BuildType.NEXTJS_STANDALONE:
            from hostkit.services.nextjs_handler import NextJSHandler

            nextjs_handler = NextJSHandler()
            files_synced = nextjs_handler.deploy_standalone(source, release_path, project)
        else:
            # Standard rsync deployment for all other types
            files_synced = self._sync_files(source, release_path, project)

        # Update file count in release record
        self.release_service.update_release_files(project, release.release_name, files_synced)

        # Step 6: Install dependencies if requested
        deps_installed = False
        if install_deps:
            # Dependencies are installed relative to the project home,
            # but the app symlink needs to be active first for nextjs
            # So we activate first, then install
            pass  # Will install after activation

        # Step 7: Activate release (atomic symlink swap)
        activated_release = self.release_service.activate_release(project, release.release_name)

        # Step 8: Link checkpoint and env snapshot to release
        self.release_service.update_release_snapshot(
            project,
            release.release_name,
            checkpoint_id=checkpoint_id,
            env_snapshot=env_snapshot,
        )

        # Step 8b: Update systemd service based on Next.js build type
        # Standalone builds need `node server.js`, regular builds need `npm start`
        if build_type == BuildType.NEXTJS_STANDALONE:
            self._update_nextjs_standalone_service(project)
        elif runtime == "nextjs":
            # Revert to npm start if previously using standalone
            self._revert_nextjs_service_to_npm(project)

        # Step 6 (continued): Now install dependencies with symlink active
        iron_session_installed = False
        if install_deps:
            deps_installed, iron_session_installed = self._install_dependencies(project, runtime)

        # Step 9: Inject secrets if requested
        secrets_injected = False
        secrets_count = 0
        if inject_secrets:
            secrets_result = self._inject_secrets(project)
            secrets_injected = secrets_result.get("total_injected", 0) > 0
            secrets_count = secrets_result.get("total_injected", 0)

        # Step 10: Restart service if requested
        service_restarted = False
        if restart and runtime != "static":
            service_restarted = self._restart_service(project)

        # Step 11: Cleanup old releases
        self.release_service.cleanup_old_releases(project)

        # Step 12: Record successful deploy in history
        duration_ms = int((time.time() - start_time) * 1000)
        rate_limit_service.record_deploy(
            project_name=project,
            success=True,
            duration_ms=duration_ms,
            source_type="rsync",
            files_synced=files_synced,
            override_used=override_ratelimit,
        )

        # Step 13: Post-deploy validation (if service was restarted)
        validation_passed = True
        validation_message = None
        if service_restarted:
            validation_result = self._validate_deploy(project)
            validation_passed = validation_result["passed"]
            validation_message = validation_result.get("message")

        return DeployResult(
            project=project,
            files_synced=files_synced,
            dependencies_installed=deps_installed,
            secrets_injected=secrets_injected,
            secrets_count=secrets_count,
            service_restarted=service_restarted,
            runtime=runtime,
            release=activated_release,
            checkpoint_id=checkpoint_id,
            env_snapshot_captured=env_snapshot_captured,
            override_used=override_ratelimit,
            build_type=build_type.value if build_type else None,
            validation_passed=validation_passed,
            validation_message=validation_message,
            app_built=app_built,
            iron_session_installed=iron_session_installed,
        )

    def deploy_from_git(
        self,
        project: str,
        repo_url: str | None = None,
        branch: str | None = None,
        tag: str | None = None,
        commit: str | None = None,
        build_app: bool = False,
        install_deps: bool = False,
        inject_secrets: bool = False,
        restart: bool = True,
        override_ratelimit: bool = False,
    ) -> DeployResult:
        """
        Deploy code to a project from a Git repository.

        Args:
            project: Project name
            repo_url: Git repository URL (or None to use configured repo)
            branch: Branch to checkout
            tag: Tag to checkout (overrides branch)
            commit: Specific commit to checkout (overrides branch/tag)
            build_app: Whether to build the app before deploying
            install_deps: Whether to install dependencies
            inject_secrets: Whether to inject secrets from vault
            restart: Whether to restart the service
            override_ratelimit: Whether to bypass rate limit checks

        Returns:
            DeployResult with deployment details
        """
        from hostkit.services.git_service import GitService, GitServiceError

        start_time = time.time()

        # Get project info
        project_info = self.db.get_project(project)
        if not project_info:
            raise DeployServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' not found",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # Initialize git service
        git_service = GitService()

        # Get repo URL from config if not provided
        if not repo_url:
            git_config = git_service.get_project_config(project)
            if not git_config:
                raise DeployServiceError(
                    code="GIT_NOT_CONFIGURED",
                    message=f"No git repository configured for project '{project}'",
                    suggestion=(
                        "Run 'hostkit git config <project> --repo <url>' or provide --git <url>"
                    ),
                )
            repo_url = git_config.repo_url
            if not branch and not tag and not commit:
                branch = git_config.default_branch

        # Step 0a: Check if project is paused (auto-pause)
        from hostkit.services.auto_pause_service import AutoPauseError, AutoPauseService

        auto_pause_service = AutoPauseService()

        try:
            auto_pause_service.check_before_deploy(project)
        except AutoPauseError as e:
            raise DeployServiceError(
                code=e.code,
                message=e.message,
                suggestion=e.suggestion,
            )

        # Step 0b: Check rate limit (unless override)
        from hostkit.services.rate_limit_service import RateLimitError, RateLimitService

        rate_limit_service = RateLimitService()

        if not override_ratelimit:
            try:
                rate_limit_service.check_rate_limit(project)
            except RateLimitError as e:
                # Record the blocked attempt
                rate_limit_service.record_deploy(
                    project_name=project,
                    success=False,
                    source_type="git",
                    error_message=e.message,
                )
                raise DeployServiceError(
                    code=e.code,
                    message=e.message,
                    suggestion=e.suggestion,
                )

        runtime = project_info.get("runtime", "python")

        # Clone repository to temp directory
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="hostkit-git-"))

            try:
                git_info = git_service.clone_to_directory(
                    repo_url=repo_url,
                    target_dir=temp_dir,
                    branch=branch,
                    tag=tag,
                    commit=commit,
                    project=project,
                )
            except GitServiceError as e:
                raise DeployServiceError(
                    code=e.code,
                    message=e.message,
                    suggestion=e.suggestion,
                )

            # Step 1: Migrate to releases if this is a legacy project
            if not self.release_service.is_release_based(project):
                self.release_service.migrate_to_releases(project)

            # Step 2: Build app if requested
            app_built = False
            if build_app:
                self._run_build(temp_dir, runtime)
                app_built = True

            # Step 3: Capture env snapshot before deploy (for rollback)
            env_snapshot = None
            env_snapshot_captured = False
            try:
                from hostkit.services.env_service import EnvService

                env_service = EnvService()
                env_snapshot = env_service.capture_snapshot(project)
                env_snapshot_captured = True
            except Exception:
                pass  # Non-fatal - env snapshot is optional

            # Step 3: Create database checkpoint if project has a database
            checkpoint_id = None
            if self._project_has_database(project):
                try:
                    from hostkit.services.checkpoint_service import CheckpointService

                    checkpoint_service = CheckpointService()
                    checkpoint = checkpoint_service.create_checkpoint(
                        project_name=project,
                        label=f"pre-deploy (git: {git_info.commit[:8]})",
                        checkpoint_type="pre_deploy",
                        trigger_source="deploy",
                    )
                    checkpoint_id = checkpoint.id
                except Exception:
                    pass  # Non-fatal - checkpoint is optional

            # Step 4: Detect build type
            detection = self.build_detector.detect(temp_dir)
            build_type = detection.build_type

            # Check for critical warnings (e.g., missing standalone node_modules)
            if detection.warning and build_type == BuildType.NEXTJS_STANDALONE:
                raise DeployServiceError(
                    code="NEXTJS_STANDALONE_INCOMPLETE",
                    message=detection.warning,
                    suggestion=(
                        "Next.js standalone builds require node_modules"
                        " in the standalone directory. Make sure the"
                        " repository includes the built"
                        " .next/standalone directory."
                    ),
                )

            # Step 5: Create new release directory
            release = self.release_service.create_release(project)
            release_path = Path(release.release_path)

            # Step 6: Sync files from temp dir to release directory based on build type
            if build_type == BuildType.NEXTJS_STANDALONE:
                from hostkit.services.nextjs_handler import NextJSHandler

                nextjs_handler = NextJSHandler()
                files_synced = nextjs_handler.deploy_standalone(temp_dir, release_path, project)
            else:
                # Standard rsync deployment for all other types
                files_synced = self._sync_files(temp_dir, release_path, project)

            # Update file count in release record
            self.release_service.update_release_files(project, release.release_name, files_synced)

            # Step 6: Update release with git info
            self.release_service.update_release_git_info(
                project,
                release.release_name,
                git_commit=git_info.commit,
                git_branch=git_info.branch,
                git_tag=git_info.tag,
                git_repo=repo_url,
            )

            # Step 7: Activate release (atomic symlink swap)
            activated_release = self.release_service.activate_release(project, release.release_name)

            # Step 8: Link checkpoint and env snapshot to release
            self.release_service.update_release_snapshot(
                project,
                release.release_name,
                checkpoint_id=checkpoint_id,
                env_snapshot=env_snapshot,
            )

            # Step 8b: Update systemd service based on Next.js build type
            # Standalone builds need `node server.js`, regular builds need `npm start`
            if build_type == BuildType.NEXTJS_STANDALONE:
                self._update_nextjs_standalone_service(project)
            elif runtime == "nextjs":
                # Revert to npm start if previously using standalone
                self._revert_nextjs_service_to_npm(project)

            # Step 9: Install dependencies if requested
            deps_installed = False
            iron_session_installed = False
            if install_deps:
                deps_installed, iron_session_installed = self._install_dependencies(
                    project, runtime
                )

            # Step 10: Inject secrets if requested
            secrets_injected = False
            secrets_count = 0
            if inject_secrets:
                secrets_result = self._inject_secrets(project)
                secrets_injected = secrets_result.get("total_injected", 0) > 0
                secrets_count = secrets_result.get("total_injected", 0)

            # Step 11: Restart service if requested
            service_restarted = False
            if restart and runtime != "static":
                service_restarted = self._restart_service(project)

            # Step 12: Cleanup old releases
            self.release_service.cleanup_old_releases(project)

            # Step 13: Record successful deploy in history
            duration_ms = int((time.time() - start_time) * 1000)
            rate_limit_service.record_deploy(
                project_name=project,
                success=True,
                duration_ms=duration_ms,
                source_type="git",
                files_synced=files_synced,
                override_used=override_ratelimit,
            )

            # Step 14: Post-deploy validation (if service was restarted)
            validation_passed = True
            validation_message = None
            if service_restarted:
                validation_result = self._validate_deploy(project)
                validation_passed = validation_result["passed"]
                validation_message = validation_result.get("message")

            return DeployResult(
                project=project,
                files_synced=files_synced,
                dependencies_installed=deps_installed,
                secrets_injected=secrets_injected,
                secrets_count=secrets_count,
                service_restarted=service_restarted,
                runtime=runtime,
                release=activated_release,
                checkpoint_id=checkpoint_id,
                env_snapshot_captured=env_snapshot_captured,
                override_used=override_ratelimit,
                git_info=git_info,
                build_type=build_type.value if build_type else None,
                validation_passed=validation_passed,
                validation_message=validation_message,
                app_built=app_built,
                iron_session_installed=iron_session_installed,
            )

        finally:
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _project_has_database(self, project: str) -> bool:
        """Check if a project has a database."""
        from hostkit.services.database_service import DatabaseService

        try:
            db_service = DatabaseService()
            return db_service.database_exists(project)
        except Exception:
            return False

    def _sync_files(self, source: Path, target: Path, project: str) -> int:
        """Rsync files to release directory.

        Args:
            source: Source directory to sync from
            target: Target release directory to sync to
            project: Project name for ownership

        Returns:
            Approximate count of files synced
        """
        # Build rsync command
        cmd = [
            "rsync",
            "-av",
            "--delete",
            "--exclude",
            "__pycache__",
            "--exclude",
            "*.pyc",
            "--exclude",
            ".git",
            "--exclude",
            "node_modules",
            "--exclude",
            ".env",
            "--exclude",
            "venv",
            "--exclude",
            ".venv",
            f"{source}/",
            f"{target}/",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            raise DeployServiceError(
                code="RSYNC_FAILED",
                message=f"Failed to sync files: {e.stderr}",
                suggestion="Check that the source directory is readable",
            )

        # Count files from rsync output (rough estimate)
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

        # Fix ownership
        try:
            subprocess.run(["chown", "-R", f"{project}:{project}", str(target)], check=True)
        except subprocess.CalledProcessError:
            # Non-fatal - files were synced but ownership may be wrong
            pass

        return file_count

    def _install_dependencies(self, project: str, runtime: str) -> tuple[bool, bool]:
        """Install dependencies based on runtime.

        For Next.js projects with auth enabled, also installs iron-session
        automatically to enable the session scaffolding.

        Returns:
            Tuple of (deps_installed, iron_session_installed)
        """
        home_dir = Path(f"/home/{project}")
        app_dir = home_dir / "app"
        iron_session_installed = False

        try:
            if runtime == "python":
                venv_dir = home_dir / "venv"
                venv_pip = venv_dir / "bin" / "pip"
                requirements = app_dir / "requirements.txt"

                # Create venv if it doesn't exist or is empty
                if not venv_pip.exists():
                    # Create the virtual environment
                    subprocess.run(
                        [
                            "sudo",
                            "-u",
                            project,
                            "python3",
                            "-m",
                            "venv",
                            str(venv_dir),
                        ],
                        check=True,
                        cwd=str(home_dir),
                    )

                # Now install requirements if they exist
                if requirements.exists() and venv_pip.exists():
                    subprocess.run(
                        [
                            "sudo",
                            "-u",
                            project,
                            str(venv_pip),
                            "install",
                            "-r",
                            str(requirements),
                        ],
                        check=True,
                        cwd=str(app_dir),
                    )
                    return True, False
                elif venv_pip.exists():
                    # Venv created but no requirements.txt - still counts as success
                    return True, False

            elif runtime in ("node", "nextjs"):
                # Both node and nextjs have package.json in app_dir
                # npm install must run where package.json lives
                package_json = app_dir / "package.json"
                if package_json.exists():
                    subprocess.run(
                        ["sudo", "-u", project, "npm", "install"],
                        check=True,
                        cwd=str(app_dir),
                    )

                    # For Next.js with auth, auto-install iron-session
                    if runtime == "nextjs":
                        iron_session_installed = self._install_iron_session_if_needed(
                            project, app_dir
                        )

                    return True, iron_session_installed

            return False, False
        except subprocess.CalledProcessError:
            return False, iron_session_installed

    def _install_iron_session_if_needed(self, project: str, work_dir: Path) -> bool:
        """Install iron-session for Next.js projects with auth enabled.

        Checks if:
        1. Auth service is enabled for this project
        2. iron-session is not already in package.json

        If both conditions are met, runs `npm install iron-session`.

        Args:
            project: Project name
            work_dir: Directory to run npm install in (app dir for Next.js)

        Returns:
            True if iron-session was installed, False otherwise
        """
        try:
            # Check if auth is enabled for this project
            auth_service = self.db.get_auth_service(project)
            if not auth_service or not auth_service.get("enabled"):
                return False

            # Check if iron-session is already in package.json
            package_json_path = work_dir / "package.json"
            if package_json_path.exists():
                import json

                try:
                    with open(package_json_path) as f:
                        package_data = json.load(f)

                    # Check both dependencies and devDependencies
                    deps = package_data.get("dependencies", {})
                    dev_deps = package_data.get("devDependencies", {})

                    if "iron-session" in deps or "iron-session" in dev_deps:
                        # Already installed, nothing to do
                        return False
                except (json.JSONDecodeError, KeyError):
                    pass  # Proceed with installation attempt

            # Install iron-session
            subprocess.run(
                ["sudo", "-u", project, "npm", "install", "iron-session"],
                check=True,
                cwd=str(work_dir),
                capture_output=True,
            )
            return True

        except subprocess.CalledProcessError:
            # Non-fatal - log but don't fail deploy
            return False
        except Exception:
            # Non-fatal - log but don't fail deploy
            return False

    def _restart_service(self, project: str) -> bool:
        """Restart the project's systemd service."""
        try:
            subprocess.run(
                ["systemctl", "restart", f"hostkit-{project}"],
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _inject_secrets(self, project: str) -> dict:
        """Inject secrets from vault into project's .env file."""
        from hostkit.services.secrets_service import SecretsService, SecretsServiceError

        try:
            secrets_service = SecretsService()
            return secrets_service.inject_secrets(project)
        except SecretsServiceError:
            # Non-fatal - return empty result
            return {"total_injected": 0, "injected": [], "skipped": []}
        except Exception:
            return {"total_injected": 0, "injected": [], "skipped": []}

    def _validate_deploy(self, project: str, timeout: int = 10) -> dict:
        """
        Validate deployment by checking if service started successfully.

        Polls for the service to reach 'active' state, with a timeout.
        This catches immediate startup failures while allowing time for
        slow-starting services.

        Args:
            project: Project name
            timeout: Maximum seconds to wait for service to stabilize

        Returns:
            Dict with:
            - passed: True if validation passed
            - message: Optional message if failed
            - status: Service status string
        """
        service_name = f"hostkit-{project}"

        # Poll for service to stabilize (check every second up to timeout)
        start_time = time.time()
        status = "unknown"

        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", service_name],
                    capture_output=True,
                    text=True,
                )
                status = result.stdout.strip()
            except Exception:
                status = "unknown"

            if status == "active":
                return {
                    "passed": True,
                    "status": status,
                }

            # If service has failed, no point waiting longer
            if status in ("failed", "inactive"):
                break

            # Still activating - wait and try again
            time.sleep(1)

        # Service not active after timeout - get recent logs for diagnosis
        logs = ""
        try:
            log_result = subprocess.run(
                ["journalctl", "-u", service_name, "-n", "20", "--no-pager"],
                capture_output=True,
                text=True,
            )
            logs = log_result.stdout
        except Exception:
            pass

        # Extract useful error info from logs
        error_hint = self._extract_error_hint(logs)

        # If still activating, it's likely a slow service - warn but pass
        if status == "activating":
            return {
                "passed": True,
                "status": status,
                "message": "Service still starting. Check logs if issues persist.",
            }

        return {
            "passed": False,
            "status": status,
            "message": f"Service status: {status}. {error_hint}".strip(),
        }

    def _update_nextjs_standalone_service(self, project: str) -> bool:
        """
        Update systemd service for Next.js standalone deployment.

        Next.js standalone builds should run with `node server.js` instead of
        `npm start`. This method updates the systemd service ExecStart command.

        Args:
            project: Project name

        Returns:
            True if service was updated, False otherwise
        """
        service_path = Path(f"/etc/systemd/system/hostkit-{project}.service")

        if not service_path.exists():
            return False

        try:
            content = service_path.read_text()

            # Check if already using node server.js
            if "node" in content and "server.js" in content:
                return False  # Already configured correctly

            # Replace npm start with node server.js for standalone
            # The server.js is at the root of the app directory after standalone deploy
            new_content = content.replace(
                "ExecStart=/usr/bin/npm start",
                f"ExecStart=/usr/bin/node /home/{project}/app/server.js",
            )

            if new_content == content:
                return False  # No changes needed

            # Write updated service file
            service_path.write_text(new_content)

            # Reload systemd
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
            )

            return True

        except Exception:
            return False

    def _revert_nextjs_service_to_npm(self, project: str) -> bool:
        """
        Revert systemd service to use npm start for non-standalone Next.js.

        When switching from standalone back to regular Next.js builds,
        the service needs to use `npm start` instead of `node server.js`.

        Args:
            project: Project name

        Returns:
            True if service was updated, False otherwise
        """
        service_path = Path(f"/etc/systemd/system/hostkit-{project}.service")

        if not service_path.exists():
            return False

        try:
            content = service_path.read_text()

            # Check if currently using node server.js (needs reverting)
            if "node" not in content or "server.js" not in content:
                return False  # Already using npm start

            # Replace node server.js with npm start
            import re

            new_content = re.sub(
                rf"ExecStart=/usr/bin/node /home/{project}/app/server\.js",
                "ExecStart=/usr/bin/npm start",
                content,
            )

            if new_content == content:
                return False  # No changes needed

            # Write updated service file
            service_path.write_text(new_content)

            # Reload systemd
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
            )

            return True

        except Exception:
            return False

    def _extract_error_hint(self, logs: str) -> str:
        """
        Extract a useful error hint from service logs.

        Looks for common error patterns to help with debugging.
        """
        if not logs:
            return ""

        # Common error patterns to look for (order matters - more specific first)
        patterns = [
            # Next.js specific - standalone node_modules missing
            (
                "Cannot find module 'next'",
                "Next.js standalone node_modules missing."
                " Check source includes"
                " .next/standalone/.../node_modules",
            ),
            # General patterns
            ("ModuleNotFoundError:", "Missing Python module. Run deploy with --install"),
            ("ImportError:", "Import error. Check dependencies with --install"),
            ("Cannot find module", "Missing Node module. Run deploy with --install"),
            ("Error: listen EADDRINUSE", "Port already in use"),
            ("ENOENT", "File or directory not found"),
            ("Permission denied", "Permission error"),
            ("SyntaxError", "Syntax error in code"),
            ("OperationalError", "Database connection error"),
            ("EACCES", "Permission denied (EACCES)"),
            ("OOMKilled", "Out of memory. Consider increasing memory limits."),
        ]

        for pattern, hint in patterns:
            if pattern in logs:
                return hint

        return "Check logs with: hostkit service logs <project>"

    def _build_app(self, project: str, source: Path, runtime: str) -> tuple[str, Path]:
        """
        Build the application in a temporary directory.

        For local source deployments, this:
        1. Creates a temp directory
        2. Copies source files to temp
        3. Runs the build (npm install && npm run build)
        4. Returns (temp_dir_path, built_source_path)

        The caller is responsible for cleaning up the temp directory.

        Args:
            project: Project name (for logging/context)
            source: Source directory to build from
            runtime: Project runtime (python, node, nextjs)

        Returns:
            Tuple of (temp_dir_path, built_source_path)

        Raises:
            DeployServiceError: If build fails
        """
        # Create temp directory for build
        build_dir = Path(tempfile.mkdtemp(prefix=f"hostkit-build-{project}-"))

        try:
            # Copy source to build directory
            # Use rsync for efficiency, excluding unnecessary files
            cmd = [
                "rsync",
                "-av",
                "--exclude",
                "__pycache__",
                "--exclude",
                "*.pyc",
                "--exclude",
                ".git",
                "--exclude",
                "node_modules",
                "--exclude",
                ".next",
                "--exclude",
                "dist",
                f"{source}/",
                f"{build_dir}/",
            ]
            subprocess.run(cmd, check=True, capture_output=True)

            # Run the build
            self._run_build(build_dir, runtime)

            return str(build_dir), build_dir

        except subprocess.CalledProcessError as e:
            # Clean up on failure
            shutil.rmtree(build_dir, ignore_errors=True)
            stderr = e.stderr.decode() if e.stderr else str(e)
            raise DeployServiceError(
                code="BUILD_FAILED",
                message=f"Build failed: {stderr[:500]}",
                suggestion="Check your build configuration and dependencies",
            )
        except DeployServiceError:
            # Clean up and re-raise
            shutil.rmtree(build_dir, ignore_errors=True)
            raise
        except Exception as e:
            # Clean up on any other failure
            shutil.rmtree(build_dir, ignore_errors=True)
            raise DeployServiceError(
                code="BUILD_FAILED",
                message=f"Build failed: {str(e)}",
                suggestion="Check your build configuration and dependencies",
            )

    def _run_build(self, build_dir: Path, runtime: str) -> None:
        """
        Run build commands in the given directory.

        Args:
            build_dir: Directory containing the source to build
            runtime: Project runtime (python, node, nextjs)

        Raises:
            DeployServiceError: If build fails
        """
        package_json = build_dir / "package.json"

        if runtime in ("node", "nextjs"):
            if not package_json.exists():
                raise DeployServiceError(
                    code="NO_PACKAGE_JSON",
                    message="No package.json found in source directory",
                    suggestion="Ensure your project has a package.json file",
                )

            # Check if there's a build script
            import json

            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                    scripts = pkg.get("scripts", {})
                    has_build = "build" in scripts
            except Exception:
                has_build = False

            if not has_build:
                raise DeployServiceError(
                    code="NO_BUILD_SCRIPT",
                    message="No 'build' script found in package.json",
                    suggestion=(
                        'Add a build script to your package.json, e.g., "build": "next build"'
                    ),
                )

            # Run npm install
            try:
                subprocess.run(
                    ["npm", "install"],
                    check=True,
                    cwd=str(build_dir),
                    capture_output=True,
                    timeout=300,  # 5 minute timeout for npm install
                )
            except subprocess.TimeoutExpired:
                raise DeployServiceError(
                    code="BUILD_TIMEOUT",
                    message="npm install timed out after 5 minutes",
                    suggestion="Check your network connection and package.json dependencies",
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode() if e.stderr else str(e)
                raise DeployServiceError(
                    code="NPM_INSTALL_FAILED",
                    message=f"npm install failed: {stderr[:500]}",
                    suggestion="Check your package.json and npm configuration",
                )

            # Run npm run build
            try:
                subprocess.run(
                    ["npm", "run", "build"],
                    check=True,
                    cwd=str(build_dir),
                    capture_output=True,
                    timeout=600,  # 10 minute timeout for build
                )
            except subprocess.TimeoutExpired:
                raise DeployServiceError(
                    code="BUILD_TIMEOUT",
                    message="npm run build timed out after 10 minutes",
                    suggestion="Check your build configuration for performance issues",
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode() if e.stderr else str(e)
                raise DeployServiceError(
                    code="BUILD_FAILED",
                    message=f"npm run build failed: {stderr[:500]}",
                    suggestion="Check your build configuration and source code for errors",
                )

        elif runtime == "python":
            # Python typically doesn't need a build step
            # But we could add support for pip install -e . or poetry build here
            pass

        else:
            # Static sites don't need a build step
            pass
