"""Capabilities command for HostKit CLI.

Exposes HostKit's capabilities as structured JSON for AI agents.
Supports both full system capabilities and project-scoped views.
"""

from dataclasses import asdict
from typing import Any

import click

from hostkit import __version__
from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.output import OutputFormatter
from hostkit.registry import CapabilitiesRegistry


@click.command("capabilities")
@click.option("--project", "-p", help="Scope to project's enabled services")
@click.option("--commands", "show_commands", is_flag=True, help="Show only commands")
@click.option("--services", "show_services", is_flag=True, help="Show only services")
@click.option("--runtimes", "show_runtimes", is_flag=True, help="Show only runtimes")
@click.option("--version-only", is_flag=True, help="Show only version")
@click.pass_context
def capabilities(
    ctx: click.Context,
    project: str | None,
    show_commands: bool,
    show_services: bool,
    show_runtimes: bool,
    version_only: bool,
) -> None:
    """Expose HostKit capabilities for AI agents.

    Without --project, shows full system capabilities including all
    available commands, services, and runtimes.

    With --project, shows capabilities scoped to that specific project,
    including only enabled services and allowed commands.

    Filter output with --commands, --services, --runtimes, or --version-only.

    Examples:

        hostkit capabilities                    # Full capabilities
        hostkit capabilities --project myapp    # Project-scoped
        hostkit capabilities --services         # Services only
        hostkit capabilities --version-only     # Version only
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    # Version-only mode
    if version_only:
        formatter.success({"version": __version__}, "HostKit version")
        return

    # Project-scoped mode
    if project:
        _show_project_capabilities(formatter, project, show_commands, show_services)
        return

    # Full capabilities mode
    _show_full_capabilities(
        formatter, show_commands, show_services, show_runtimes
    )


def _show_full_capabilities(
    formatter: OutputFormatter,
    show_commands: bool,
    show_services: bool,
    show_runtimes: bool,
) -> None:
    """Display full system capabilities."""
    # Lazy import to avoid circular imports
    from hostkit.cli import cli as cli_group
    from hostkit.services.introspection_service import introspect_cli

    # Introspect CLI tree
    commands = introspect_cli(cli_group)

    # Get services and runtimes from registry
    services = {k: asdict(v) for k, v in CapabilitiesRegistry.get_services().items()}
    runtimes = {k: asdict(v) for k, v in CapabilitiesRegistry.get_runtimes().items()}

    # Build response data
    data: dict[str, Any] = {
        "version": __version__,
        "vps": get_config().vps_ip,
        "operator_user": "ai-operator",
        "wildcard_domain": {
            "pattern": "{project}.hostkit.dev",
            "ssl": "automatic (wildcard cert)",
            "dns": "automatic (wildcard A record)",
            "note": "Every new project automatically gets a working subdomain with SSL - no setup required",
        },
        "auth_callbacks": {
            "pattern": "https://{project}.hostkit.dev/auth/oauth/{provider}/callback",
            "providers": ["google", "apple"],
            "note": "Auth always uses hostkit.dev subdomain for OAuth callbacks - predictable URLs for provider console setup",
        },
    }

    # Apply filters if specified
    if show_commands or show_services or show_runtimes:
        if show_commands:
            data["commands"] = commands
        if show_services:
            data["services"] = services
        if show_runtimes:
            data["runtimes"] = runtimes
    else:
        # No filters - show everything
        data["commands"] = commands
        data["services"] = services
        data["runtimes"] = runtimes

    formatter.success(data, "HostKit capabilities")


def _show_project_capabilities(
    formatter: OutputFormatter,
    project_name: str,
    show_commands: bool,
    show_services: bool,
) -> None:
    """Display project-scoped capabilities."""
    db = get_db()

    # Get project info
    project = db.get_project(project_name)
    if not project:
        formatter.error(
            code="PROJECT_NOT_FOUND",
            message=f"Project '{project_name}' does not exist",
            suggestion="Run 'hostkit project list' to see available projects",
        )

    # Check which services are enabled for this project
    enabled_services = _get_project_services(project_name, project)

    # Get allowed commands for this project
    allowed_commands = _get_project_commands(project_name)

    # Build response
    default_domain = f"{project_name}.hostkit.dev"
    data: dict[str, Any] = {
        "project": project_name,
        "runtime": project["runtime"],
        "default_domain": default_domain,
        "urls": {
            "app": f"https://{default_domain}",
            "auth": f"https://{default_domain}/auth/",
            "oauth_callbacks": {
                "google": f"https://{default_domain}/auth/oauth/google/callback",
                "apple": f"https://{default_domain}/auth/oauth/apple/callback",
            },
        },
    }

    # Apply filters if specified
    if show_commands or show_services:
        if show_services:
            data["enabled_services"] = enabled_services
        if show_commands:
            data["allowed_commands"] = allowed_commands
    else:
        # No filters - show both
        data["enabled_services"] = enabled_services
        data["allowed_commands"] = allowed_commands

    formatter.success(data, f"Capabilities for project '{project_name}'")


def _get_project_services(project_name: str, project: dict[str, Any]) -> dict[str, Any]:
    """Get enabled services for a project.

    Args:
        project_name: Name of the project
        project: Project record from database

    Returns:
        Dictionary mapping service names to their status and env vars
    """
    db = get_db()
    services: dict[str, Any] = {}

    # Check database service
    if project.get("redis_db") is not None:
        # If redis_db is set, database is provisioned
        services["database"] = {
            "enabled": True,
            "env_vars": ["DATABASE_URL"],
        }
    else:
        services["database"] = {"enabled": False}

    # Check Redis service
    if project.get("redis_db") is not None:
        services["redis"] = {
            "enabled": True,
            "env_vars": ["REDIS_URL"],
        }
    else:
        services["redis"] = {"enabled": False}

    # Check auth service
    auth_service = db.get_auth_service(project_name)
    if auth_service and auth_service.get("enabled"):
        auth_port = auth_service.get("auth_port")

        # Build OAuth providers info
        oauth_providers: dict[str, Any] = {}

        google_client_id = auth_service.get("google_client_id")
        if google_client_id:
            oauth_providers["google"] = {
                "configured": True,
                "client_id": f"{google_client_id[:15]}..." if len(google_client_id) > 15 else google_client_id,
            }
        else:
            oauth_providers["google"] = {"configured": False}

        apple_client_id = auth_service.get("apple_client_id")
        if apple_client_id:
            oauth_providers["apple"] = {
                "configured": True,
                "client_id": f"{apple_client_id[:15]}..." if len(apple_client_id) > 15 else apple_client_id,
            }
        else:
            oauth_providers["apple"] = {"configured": False}

        services["auth"] = {
            "enabled": True,
            "port": auth_port,
            "env_vars": [
                "AUTH_URL",
                "AUTH_JWT_PUBLIC_KEY",
            ],
            "log_file": f"/var/log/projects/{project_name}/auth.log",
            "oauth_providers": oauth_providers,
            "commands": {
                "config": f"hostkit auth config {project_name}",
                "logs": f"hostkit auth logs {project_name}",
                "restart": f"sudo /bin/systemctl restart hostkit-{project_name}-auth.service",
            },
            "native_oauth": {
                "google": {
                    "endpoint": "POST /auth/oauth/google/verify-token",
                    "params": ["id_token (required)", "ios_client_id (required for iOS)", "access_token (optional, for at_hash validation)"],
                },
                "apple": {
                    "endpoint": "POST /auth/oauth/apple/verify-token",
                    "params": ["id_token (required)", "bundle_id (optional, defaults to APPLE_BUNDLE_ID)"],
                },
                "notes": "iOS apps must use singleton AppState pattern to prevent OAuth scene fragmentation",
            },
        }
    else:
        services["auth"] = {"enabled": False}

    # Check domains/nginx
    domains = db.list_domains(project_name)
    if domains:
        services["nginx"] = {
            "enabled": True,
            "domains": [d["domain"] for d in domains],
        }
    else:
        services["nginx"] = {"enabled": False}

    # Check mail service
    try:
        from hostkit.services.mail_service import MailService

        mail_service = MailService()
        if mail_service.is_project_mail_enabled(project_name):
            services["mail"] = {
                "enabled": True,
                "env_vars": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"],
            }
        else:
            services["mail"] = {"enabled": False}
    except Exception:
        services["mail"] = {"enabled": False}

    # Check secrets service
    try:
        from hostkit.services.secrets_service import SecretsService

        secrets_service = SecretsService()
        verify_result = secrets_service.verify_secrets(project_name)
        if verify_result.get("required_count", 0) > 0:
            services["secrets"] = {
                "enabled": True,
                "required_count": verify_result.get("required_count", 0),
                "required_set": verify_result.get("required_set", 0),
            }
        else:
            services["secrets"] = {"enabled": False}
    except Exception:
        services["secrets"] = {"enabled": False}

    # Check vector service
    # Vector is enabled if project has a vector_projects record
    try:
        import sqlite3
        from hostkit.config import get_config

        config = get_config()
        vector_db_path = config.paths.data_dir / "vector.db"

        if vector_db_path.exists():
            conn = sqlite3.connect(vector_db_path)
            cursor = conn.execute(
                "SELECT id FROM vector_projects WHERE project_name = ?",
                (project_name,)
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                services["vector"] = {
                    "enabled": True,
                    "env_vars": ["VECTOR_API_URL", "VECTOR_API_KEY"],
                }
            else:
                services["vector"] = {"enabled": False}
        else:
            services["vector"] = {"enabled": False}
    except Exception:
        services["vector"] = {"enabled": False}

    # Image service - available to all projects by default
    services["image"] = {
        "enabled": True,
        "description": "AI image generation via Black Forest Labs Flux API",
        "models": ["flux-1.1-pro", "flux-1.1-pro-ultra"],
        "rate_limits": {"hourly": 100, "daily": 500},
        "commands": {
            "generate": f"hostkit image generate {project_name} '<prompt>'",
            "usage": f"hostkit image usage {project_name}",
            "history": f"hostkit image history {project_name}",
            "models": "hostkit image models",
        },
    }

    return services


def _get_project_commands(project_name: str) -> dict[str, Any]:
    """Get allowed commands for a project.

    Returns a simplified command set that project agents can use.
    Excludes operator-only commands like project create/delete.

    Args:
        project_name: Name of the project

    Returns:
        Dictionary mapping command categories to their commands
    """
    commands = {
        "deployment": {
            "deploy": {
                "command": f"hostkit deploy {project_name} --source ./code",
                "description": "Deploy code to the project",
            },
            "rollback": {
                "command": f"hostkit rollback {project_name}",
                "description": "Rollback to previous deployment",
            },
            "deploys": {
                "command": f"hostkit deploys {project_name}",
                "description": "List deployment history",
            },
        },
        "monitoring": {
            "health": {
                "command": f"hostkit health {project_name}",
                "description": "Check project health",
            },
            "status": {
                "command": f"hostkit status {project_name}",
                "description": "Show project status",
            },
            "diagnose": {
                "command": f"hostkit diagnose {project_name}",
                "description": "Run diagnostics",
            },
            "logs": {
                "command": f"hostkit service logs {project_name}",
                "description": "View project logs",
            },
        },
        "service": {
            "start": {
                "command": f"hostkit service start {project_name}",
                "description": "Start project service",
            },
            "stop": {
                "command": f"hostkit service stop {project_name}",
                "description": "Stop project service",
            },
            "restart": {
                "command": f"hostkit service restart {project_name}",
                "description": "Restart project service",
            },
        },
        "environment": {
            "env_set": {
                "command": f"hostkit env set {project_name} KEY=value",
                "description": "Set environment variable",
            },
            "env_get": {
                "command": f"hostkit env get {project_name} KEY",
                "description": "Get environment variable",
            },
            "env_list": {
                "command": f"hostkit env list {project_name}",
                "description": "List environment variables",
            },
        },
        "database": {
            "db_shell": {
                "command": f"hostkit db shell {project_name}",
                "description": "Open database shell",
            },
            "db_backup": {
                "command": f"hostkit db backup {project_name}",
                "description": "Create database backup",
            },
            "db_restore": {
                "command": f"hostkit db restore {project_name} <backup_file>",
                "description": "Restore database from backup",
            },
        },
        "auth": {
            "auth_config": {
                "command": f"hostkit auth config {project_name}",
                "description": "View/update auth configuration",
            },
            "auth_logs": {
                "command": f"hostkit auth logs {project_name}",
                "description": "View auth service logs",
            },
            "auth_status": {
                "command": f"hostkit auth status {project_name}",
                "description": "Show auth service status",
            },
            "auth_users": {
                "command": f"hostkit auth users {project_name}",
                "description": "List auth service users",
            },
            "auth_export_key": {
                "command": f"hostkit auth export-key {project_name} --update-env",
                "description": "Sync JWT public key to .env",
            },
        },
        "image": {
            "generate": {
                "command": f"hostkit image generate {project_name} '<prompt>'",
                "description": "Generate image from text prompt",
            },
            "usage": {
                "command": f"hostkit image usage {project_name}",
                "description": "Show image generation usage/limits",
            },
            "history": {
                "command": f"hostkit image history {project_name}",
                "description": "Show recent image generations",
            },
            "models": {
                "command": "hostkit image models",
                "description": "List available image models",
            },
        },
    }

    return commands
