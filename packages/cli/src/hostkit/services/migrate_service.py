"""Database migration service for HostKit.

Handles auto-detection and execution of database migrations
for various frameworks (Django, Alembic, Prisma).
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hostkit.database import get_db
from hostkit.services.env_service import EnvService


# Migration framework detection and commands
MIGRATION_FRAMEWORKS: dict[str, dict[str, list[str] | str]] = {
    "django": {
        "detect": ["manage.py"],
        "command": "{python} manage.py migrate",
    },
    "alembic": {
        "detect": ["alembic.ini"],
        "command": "{python} -m alembic upgrade head",
    },
    "prisma": {
        "detect": ["prisma/schema.prisma"],
        "command": "npx prisma migrate deploy",
    },
}


@dataclass
class MigrationResult:
    """Result of a migration operation."""

    project: str
    framework: str
    command: str
    success: bool
    output: str
    dry_run: bool


class MigrateServiceError(Exception):
    """Exception for migration service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class MigrateService:
    """Service for running database migrations."""

    def __init__(self) -> None:
        self.db = get_db()

    def _get_python_path(self, project: str) -> str:
        """Get the Python executable path for a project.

        Args:
            project: Project name

        Returns:
            Path to Python in the project's virtualenv, or system python
        """
        venv_python = Path(f"/home/{project}/venv/bin/python")
        if venv_python.exists():
            return str(venv_python)
        return "python3"

    def _get_app_dir(self, project: str) -> Path:
        """Get the app directory for a project."""
        return Path(f"/home/{project}/app")

    def detect_framework(self, project: str) -> str | None:
        """Auto-detect the migration framework for a project.

        Checks for framework-specific files in the project's app directory.

        Args:
            project: Project name

        Returns:
            Framework name ('django', 'alembic', 'prisma') or None
        """
        # Validate project exists
        if not self.db.get_project(project):
            raise MigrateServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        app_dir = self._get_app_dir(project)
        if not app_dir.exists():
            return None

        for framework, config in MIGRATION_FRAMEWORKS.items():
            detect_files = config["detect"]
            if isinstance(detect_files, list):
                for detect_file in detect_files:
                    if (app_dir / detect_file).exists():
                        return framework

        return None

    def migrate(
        self,
        project: str,
        framework: str | None = None,
        custom_cmd: str | None = None,
        dry_run: bool = False,
    ) -> MigrationResult:
        """Run database migrations for a project.

        Args:
            project: Project name
            framework: Explicit framework ('django', 'alembic', 'prisma')
                       If None, auto-detect
            custom_cmd: Custom migration command to run
            dry_run: If True, show what would be run without executing

        Returns:
            MigrationResult with migration details
        """
        # Validate project exists
        project_info = self.db.get_project(project)
        if not project_info:
            raise MigrateServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        app_dir = self._get_app_dir(project)
        if not app_dir.exists():
            raise MigrateServiceError(
                code="APP_DIR_NOT_FOUND",
                message=f"App directory does not exist: {app_dir}",
                suggestion="Deploy the project first with 'hostkit deploy'",
            )

        # Determine the command to run
        if custom_cmd:
            # Custom command provided
            cmd_template = custom_cmd
            framework_name = "custom"
        elif framework:
            # Explicit framework specified
            if framework not in MIGRATION_FRAMEWORKS:
                raise MigrateServiceError(
                    code="UNKNOWN_FRAMEWORK",
                    message=f"Unknown migration framework: {framework}",
                    suggestion=f"Supported frameworks: {', '.join(MIGRATION_FRAMEWORKS.keys())}",
                )
            cmd_template = str(MIGRATION_FRAMEWORKS[framework]["command"])
            framework_name = framework
        else:
            # Auto-detect framework
            detected = self.detect_framework(project)
            if not detected:
                raise MigrateServiceError(
                    code="NO_FRAMEWORK_DETECTED",
                    message="Could not auto-detect migration framework",
                    suggestion="Specify --django, --alembic, --prisma, or use --cmd",
                )
            cmd_template = str(MIGRATION_FRAMEWORKS[detected]["command"])
            framework_name = detected

        # Substitute {python} in command
        python_path = self._get_python_path(project)
        command = cmd_template.replace("{python}", python_path)

        # For dry run, just return what would be executed
        if dry_run:
            return MigrationResult(
                project=project,
                framework=framework_name,
                command=command,
                success=True,
                output=f"Would run: {command}\nIn directory: {app_dir}",
                dry_run=True,
            )

        # Execute the migration command
        try:
            # Load project's environment variables from .env file
            env_service = EnvService()
            project_env = env_service._read_env_file(project)

            # Build environment: system env + project env + overrides
            run_env = os.environ.copy()
            run_env.update(project_env)
            run_env["HOME"] = f"/home/{project}"
            run_env["USER"] = project

            result = subprocess.run(
                command,
                shell=True,
                cwd=str(app_dir),
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                env=run_env,
            )

            output = result.stdout
            if result.stderr:
                output += f"\n{result.stderr}"

            return MigrationResult(
                project=project,
                framework=framework_name,
                command=command,
                success=result.returncode == 0,
                output=output.strip(),
                dry_run=False,
            )

        except subprocess.TimeoutExpired:
            raise MigrateServiceError(
                code="MIGRATION_TIMEOUT",
                message="Migration timed out after 5 minutes",
                suggestion="Check for long-running migrations or locks",
            )
        except Exception as e:
            raise MigrateServiceError(
                code="MIGRATION_FAILED",
                message=f"Failed to run migration: {e}",
                suggestion="Check the migration command and database connectivity",
            )
