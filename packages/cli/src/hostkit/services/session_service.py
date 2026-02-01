"""Session scaffolding service for Next.js projects.

Provides iron-session based session management templates for Next.js
projects that use HostKit Auth.
"""

import os
import secrets
import shutil
import subprocess
from pathlib import Path

from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db


class SessionServiceError(Exception):
    """Base exception for session service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_session_secret(length: int = 32) -> str:
    """Generate a cryptographically secure session secret.

    iron-session requires at least 32 characters.
    """
    return secrets.token_hex(length)


class SessionService:
    """Service for managing iron-session scaffolding in Next.js projects."""

    def __init__(self) -> None:
        """Initialize the session service."""
        self.config = get_config()
        self.db = get_db()

    def _get_templates_dir(self) -> Path:
        """Get the path to the nextjs-session templates directory."""
        # In development, templates are relative to the package
        dev_path = Path(__file__).parent.parent.parent.parent / "templates" / "nextjs-session"
        prod_path = Path("/var/lib/hostkit/templates/nextjs-session")

        if prod_path.exists():
            return prod_path
        elif dev_path.exists():
            return dev_path
        else:
            raise SessionServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Next.js session templates not found",
                suggestion="Ensure HostKit is properly installed with templates synced",
            )

    def _project_home(self, project: str) -> Path:
        """Get the project's home directory."""
        return Path(f"/home/{project}")

    def is_session_scaffolded(self, project: str) -> bool:
        """Check if session scaffolding already exists in a project."""
        home = self._project_home(project)
        session_file = home / "app" / "lib" / "session.ts"
        return session_file.exists()

    def scaffold_session(self, project: str, force: bool = False) -> dict:
        """Deploy iron-session scaffolding to a Next.js project.

        This creates:
        - lib/session.ts - Core iron-session configuration
        - lib/auth.ts - HostKit Auth API helpers
        - types/auth.ts - TypeScript types
        - app/api/session/route.ts - Session API routes
        - middleware.ts - Route protection
        - contexts/AuthContext.tsx - Client-side auth context

        Args:
            project: Project name
            force: Overwrite existing files if True

        Returns:
            Dict with scaffolding details
        """
        # Verify project exists
        project_data = self.db.get_project(project)
        if not project_data:
            raise SessionServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Verify it's a Next.js project
        if project_data["runtime"] != "nextjs":
            raise SessionServiceError(
                code="WRONG_RUNTIME",
                message=f"Project '{project}' is not a Next.js project (runtime: {project_data['runtime']})",
                suggestion="Session scaffolding is only for Next.js projects",
            )

        # Check if already scaffolded
        if self.is_session_scaffolded(project) and not force:
            raise SessionServiceError(
                code="ALREADY_SCAFFOLDED",
                message=f"Session scaffolding already exists for '{project}'",
                suggestion="Use --force to overwrite existing files",
            )

        templates_dir = self._get_templates_dir()
        home = self._project_home(project)
        app_dir = home / "app"

        # Template context for Jinja2
        context = {
            "project_name": project,
        }

        # Create directories
        dirs_to_create = [
            app_dir / "lib",
            app_dir / "types",
            app_dir / "app" / "api" / "session",
            app_dir / "contexts",
        ]
        for dir_path in dirs_to_create:
            dir_path.mkdir(parents=True, exist_ok=True)

        files_created = []

        # Copy and render templates
        template_mappings = [
            ("lib/session.ts.j2", app_dir / "lib" / "session.ts"),
            ("lib/auth.ts.j2", app_dir / "lib" / "auth.ts"),
            ("types/auth.ts", app_dir / "types" / "auth.ts"),
            ("app/api/session/route.ts", app_dir / "app" / "api" / "session" / "route.ts"),
            ("middleware.ts.j2", app_dir / "middleware.ts"),
            ("contexts/AuthContext.tsx.j2", app_dir / "contexts" / "AuthContext.tsx"),
        ]

        for src_rel, dest_path in template_mappings:
            src_path = templates_dir / src_rel

            if not src_path.exists():
                continue

            # Ensure parent directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if it's a Jinja2 template
            if src_rel.endswith(".j2"):
                template = Template(src_path.read_text())
                content = template.render(**context)
                dest_path.write_text(content)
            else:
                shutil.copy2(src_path, dest_path)

            files_created.append(str(dest_path.relative_to(home)))

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(app_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        return {
            "project": project,
            "files_created": files_created,
            "app_dir": str(app_dir),
        }

    def add_session_secret_to_env(self, project: str) -> str:
        """Generate and add SESSION_SECRET to project's .env file.

        Also adds NEXT_PUBLIC_AUTH_URL for client-side auth API calls.

        Args:
            project: Project name

        Returns:
            The generated session secret
        """
        env_path = self._project_home(project) / ".env"

        if not env_path.exists():
            raise SessionServiceError(
                code="ENV_NOT_FOUND",
                message=f"Environment file not found: {env_path}",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if SESSION_SECRET already exists
        content = env_path.read_text()
        if "SESSION_SECRET=" in content:
            # Find and return existing secret
            for line in content.splitlines():
                if line.startswith("SESSION_SECRET="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")

        # Generate new secret
        session_secret = generate_session_secret()

        # Build session block with auth URL
        auth_url = f"https://{project}.hostkit.dev"
        session_block = f"""
# iron-session (HostKit managed)
SESSION_SECRET={session_secret}
NEXT_PUBLIC_AUTH_URL={auth_url}
"""
        with open(env_path, "a") as f:
            f.write(session_block)

        # Ensure correct ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        return session_secret

    def setup_nextjs_auth(self, project: str) -> dict:
        """Full setup for Next.js project with auth.

        Combines scaffolding and environment setup.

        Args:
            project: Project name

        Returns:
            Setup details
        """
        # Generate session secret first
        session_secret = self.add_session_secret_to_env(project)

        # Deploy scaffolding
        scaffold_result = self.scaffold_session(project, force=False)

        return {
            "project": project,
            "session_secret_added": True,
            "files_created": scaffold_result["files_created"],
            "next_steps": [
                "Install iron-session: npm install iron-session",
                "Update tsconfig.json paths if using @/ imports",
                "Wrap your app with AuthProvider in layout.tsx",
            ],
        }
