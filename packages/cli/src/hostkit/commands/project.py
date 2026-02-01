"""Project management commands for HostKit CLI."""

from typing import Any

import click

from hostkit.access import operator_or_root, project_access, root_only
from hostkit.output import OutputFormatter
from hostkit.services.project_service import ProjectService, ProjectServiceError


@click.group("project")
def project() -> None:
    """Manage HostKit projects.

    Projects are isolated environments with their own Linux user,
    home directory, systemd service, and assigned port.
    """
    pass


@project.command("create")
@click.argument("name")
@click.option(
    "--python",
    "runtime",
    flag_value="python",
    default=True,
    help="Create a Python project (default)",
)
@click.option(
    "--node",
    "runtime",
    flag_value="node",
    help="Create a Node.js project",
)
@click.option(
    "--nextjs",
    "runtime",
    flag_value="nextjs",
    help="Create a Next.js project",
)
@click.option(
    "--static",
    "runtime",
    flag_value="static",
    help="Create a static site project",
)
@click.option(
    "--description", "-d",
    help="Project description",
)
@click.option(
    "--start-cmd",
    help="Custom start command (overrides default for runtime)",
)
@click.option(
    "--with-db",
    is_flag=True,
    help="Also create a PostgreSQL database for this project",
)
@click.option(
    "--with-storage",
    is_flag=True,
    help="Also create a MinIO storage bucket for this project",
)
@click.option(
    "--with-auth",
    is_flag=True,
    help="Enable authentication service for this project",
)
@click.option(
    "--with-booking",
    is_flag=True,
    help="Enable booking/scheduling service for this project",
)
@click.option(
    "--with-sms",
    is_flag=True,
    help="Enable SMS service for this project",
)
@click.option(
    "--with-mail",
    is_flag=True,
    help="Enable mail service for this project",
)
@click.option(
    "--with-payments",
    is_flag=True,
    help="Enable payments (Stripe) service for this project",
)
@click.option(
    "--with-chatbot",
    is_flag=True,
    help="Enable AI chatbot service for this project",
)
@click.option(
    "--with-r2",
    is_flag=True,
    help="Enable Cloudflare R2 storage for this project",
)
@click.option(
    "--with-vector",
    is_flag=True,
    help="Enable vector/RAG service for this project",
)
@click.option(
    "--google-client-id",
    help="Google OAuth client ID (requires --with-auth)",
)
@click.option(
    "--google-client-secret",
    help="Google OAuth client secret (requires --with-auth)",
)
@click.pass_context
@operator_or_root
def create(
    ctx: click.Context,
    name: str,
    runtime: str,
    description: str | None,
    start_cmd: str | None,
    with_db: bool,
    with_storage: bool,
    with_auth: bool,
    with_booking: bool,
    with_sms: bool,
    with_mail: bool,
    with_payments: bool,
    with_chatbot: bool,
    with_r2: bool,
    with_vector: bool,
    google_client_id: str | None,
    google_client_secret: str | None,
) -> None:
    """Create a new project.

    Creates a Linux user, home directory, systemd service, and assigns
    a port number. The project is ready for code deployment.

    NAME must be 3-32 characters, lowercase alphanumeric, starting with a letter.

    Examples:
        hostkit project create myapp
        hostkit project create api --node
        hostkit project create frontend --nextjs
        hostkit project create docs --static -d "Documentation site"
        hostkit project create webapp --with-db
        hostkit project create media --with-storage
        hostkit project create myapp --with-auth
        hostkit project create myapp --with-auth --google-client-id=xxx --google-client-secret=yyy
        hostkit project create fullstack --python --with-db --with-auth
        hostkit project create custom --node --start-cmd "/usr/bin/node /home/{project_name}/app/server.js"
        hostkit project create spa --nextjs --with-db --with-auth --with-booking --with-payments
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        project_info = service.create_project(
            name,
            runtime,
            description,
            create_storage=with_storage,
            start_cmd=start_cmd,
        )

        data: dict[str, Any] = {
            "name": project_info.name,
            "runtime": project_info.runtime,
            "port": project_info.port,
            "redis_db": project_info.redis_db,
            "home": f"/home/{project_info.name}",
            "service": f"hostkit-{project_info.name}",
            "services_enabled": [],
        }

        # Note if storage was created
        if with_storage:
            data["storage_bucket"] = f"{name}-storage"
            data["services_enabled"].append("storage")

        # Create database if requested
        if with_db:
            try:
                from hostkit.services.database_service import (
                    DatabaseService,
                    DatabaseServiceError,
                )

                db_service = DatabaseService()
                credentials = db_service.create_database(name)
                db_service.update_project_env(name, credentials)
                data["database"] = credentials.database
                data["database_user"] = credentials.username
                data["services_enabled"].append("database")

                # If --with-vector is also set, enable pgvector in the main database
                # This allows embedding columns directly in project tables (vs separate vector DB)
                if with_vector:
                    try:
                        ext_result = db_service.enable_extension(name, "vector")
                        data["pgvector_enabled"] = True
                        data["pgvector_status"] = ext_result.get("status", "enabled")
                    except DatabaseServiceError as e:
                        click.echo(f"Warning: Could not enable pgvector: {e.message}", err=True)
                        data["pgvector_enabled"] = False

            except DatabaseServiceError as e:
                click.echo(f"Warning: Could not create database: {e.message}", err=True)
            except Exception as e:
                click.echo(f"Warning: Could not create database: {e}", err=True)

        # Enable authentication if requested
        if with_auth:
            try:
                from hostkit.services.auth_service import AuthService

                auth_service = AuthService()
                auth_config = auth_service.enable_auth(
                    project=name,
                    google_client_id=google_client_id,
                    google_client_secret=google_client_secret,
                )
                data["auth_enabled"] = True
                data["auth_port"] = auth_config.port
                data["services_enabled"].append("auth")

                # For Next.js projects, also scaffold iron-session
                if runtime == "nextjs":
                    try:
                        from hostkit.services.session_service import SessionService

                        session_service = SessionService()
                        session_result = session_service.setup_nextjs_auth(name)
                        data["session_scaffolded"] = True
                        data["session_files"] = session_result["files_created"]
                    except Exception as e:
                        click.echo(f"Warning: Could not scaffold iron-session: {e}", err=True)
                        data["session_scaffolded"] = False

            except Exception as e:
                click.echo(f"Warning: Could not enable auth: {e}", err=True)
                data["auth_enabled"] = False

        # Enable booking if requested
        if with_booking:
            try:
                from hostkit.services.booking_service import BookingService

                booking_service = BookingService()
                booking_result = booking_service.enable_booking(name)
                data["booking_enabled"] = True
                data["booking_port"] = booking_result.get("port")
                data["services_enabled"].append("booking")
            except Exception as e:
                click.echo(f"Warning: Could not enable booking: {e}", err=True)
                data["booking_enabled"] = False

        # Enable SMS if requested
        if with_sms:
            try:
                from hostkit.services.sms_service import SMSService

                sms_service = SMSService()
                sms_result = sms_service.enable_sms(name)
                data["sms_enabled"] = True
                data["sms_port"] = sms_result.get("port")
                data["services_enabled"].append("sms")
            except Exception as e:
                click.echo(f"Warning: Could not enable SMS: {e}", err=True)
                data["sms_enabled"] = False

        # Enable mail if requested
        if with_mail:
            try:
                from hostkit.services.mail_service import MailService

                mail_service = MailService()
                mail_result = mail_service.enable_project_mail(name)
                data["mail_enabled"] = True
                data["mail_address"] = mail_result.get("default_address")
                data["services_enabled"].append("mail")
            except Exception as e:
                click.echo(f"Warning: Could not enable mail: {e}", err=True)
                data["mail_enabled"] = False

        # Enable payments if requested
        if with_payments:
            try:
                from hostkit.services.payment_service import PaymentService

                payment_service = PaymentService()
                payments_result = payment_service.enable_payments(name)
                data["payments_enabled"] = True
                data["payments_port"] = payments_result.get("port")
                data["services_enabled"].append("payments")
            except Exception as e:
                click.echo(f"Warning: Could not enable payments: {e}", err=True)
                data["payments_enabled"] = False

        # Enable chatbot if requested
        if with_chatbot:
            try:
                from hostkit.services.chatbot_service import ChatbotService

                chatbot_service = ChatbotService()
                chatbot_result = chatbot_service.enable_chatbot(name)
                data["chatbot_enabled"] = True
                data["chatbot_port"] = chatbot_result.get("port")
                data["services_enabled"].append("chatbot")
            except Exception as e:
                click.echo(f"Warning: Could not enable chatbot: {e}", err=True)
                data["chatbot_enabled"] = False

        # Enable R2 storage if requested
        if with_r2:
            try:
                from hostkit.services.r2_service import R2Service

                r2_service = R2Service()
                r2_result = r2_service.enable(name)
                data["r2_enabled"] = True
                data["r2_bucket"] = r2_result.get("bucket")
                data["services_enabled"].append("r2")
            except Exception as e:
                click.echo(f"Warning: Could not enable R2: {e}", err=True)
                data["r2_enabled"] = False

        # Enable vector if requested
        if with_vector:
            try:
                from hostkit.services.vector_service import VectorService

                vector_service = VectorService()
                vector_result = vector_service.enable_project(name)
                data["vector_enabled"] = True
                data["services_enabled"].append("vector")
            except Exception as e:
                click.echo(f"Warning: Could not enable vector: {e}", err=True)
                data["vector_enabled"] = False

        # Build success message
        services_str = ", ".join(data["services_enabled"]) if data["services_enabled"] else "none"
        formatter.success(data, f"Project '{name}' created successfully (services: {services_str})")

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@project.command("delete")
@click.argument("name")
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Confirm deletion (required)",
)
@click.pass_context
@operator_or_root
def delete(ctx: click.Context, name: str, force: bool) -> None:
    """Delete a project and all its resources.

    This will:
    - Stop the project's service
    - Remove the systemd service file
    - Delete the Linux user and home directory
    - Remove log files
    - Delete PostgreSQL database if exists
    - Delete database records (including domains and backups)

    Requires --force flag to confirm deletion.

    Example:
        hostkit project delete myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        # Try to delete database if it exists (best effort)
        db_deleted = False
        try:
            from hostkit.services.database_service import (
                DatabaseService,
                DatabaseServiceError,
            )

            db_service = DatabaseService()
            if db_service.database_exists(name):
                db_service.delete_database(name, force=force)
                db_deleted = True
        except DatabaseServiceError:
            pass  # Database might not exist or can't connect, continue
        except Exception:
            pass  # PostgreSQL not available, continue

        # Note: Storage bucket cleanup is handled in project_service.delete_project()
        service.delete_project(name, force)
        formatter.success(
            {"name": name, "database_deleted": db_deleted, "storage_cleaned": True},
            f"Project '{name}' deleted successfully",
        )

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@project.command("list")
@click.pass_context
def list_projects(ctx: click.Context) -> None:
    """List all projects.

    Shows project name, runtime, port, and current status.
    With --json, also includes enabled services and URL.

    Example:
        hostkit project list
        hostkit --json project list
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    projects = service.list_projects()

    if not projects:
        formatter.success([], "No projects found")
        return

    # Build data - JSON mode gets services and url
    data = []
    for p in projects:
        project_data: dict[str, Any] = {
            "name": p.name,
            "runtime": p.runtime,
            "status": p.status,
        }
        if formatter.json_mode:
            project_data["services"] = service.get_project_enabled_services(p.name)
            project_data["url"] = f"https://{p.name}.hostkit.dev"
        else:
            project_data["port"] = p.port
            project_data["created_at"] = p.created_at[:10]
        data.append(project_data)

    columns = [
        ("name", "Name"),
        ("runtime", "Runtime"),
        ("port", "Port"),
        ("status", "Status"),
        ("created_at", "Created"),
    ]

    formatter.table(data, columns, title="Projects", message="Projects retrieved")


@project.command("info")
@click.argument("name")
@click.pass_context
@project_access("name")
def info(ctx: click.Context, name: str) -> None:
    """Show detailed information about a project.

    Displays:
    - Basic project info (runtime, port, status)
    - Service configuration
    - File paths
    - Resource usage
    - Associated domains
    - Recent backups

    Example:
        hostkit project info myapp
        hostkit --json project info myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        details = service.get_project_details(name)

        formatter.status_panel(
            f"Project: {name}",
            details,
            message=f"Project '{name}' info retrieved",
        )

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@project.command("start")
@click.argument("name")
@click.pass_context
@project_access("name")
def start(ctx: click.Context, name: str) -> None:
    """Start a project's service.

    Example:
        hostkit project start myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        # Verify project exists
        project_info = service.get_project(name)

        # Start the service
        import subprocess
        subprocess.run(
            ["systemctl", "start", f"hostkit-{name}"],
            check=True,
            capture_output=True,
        )

        # Update status
        from hostkit.database import get_db
        get_db().update_project_status(name, "running")

        formatter.success(
            {"name": name, "service": f"hostkit-{name}"},
            f"Project '{name}' started",
        )

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)
    except subprocess.CalledProcessError as e:
        formatter.error(
            "SERVICE_START_FAILED",
            f"Failed to start service: {e.stderr.decode() if e.stderr else 'unknown error'}",
            f"Check logs with: journalctl -u hostkit-{name}",
        )


@project.command("stop")
@click.argument("name")
@click.pass_context
@project_access("name")
def stop(ctx: click.Context, name: str) -> None:
    """Stop a project's service.

    Example:
        hostkit project stop myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        # Verify project exists
        project_info = service.get_project(name)

        # Stop the service
        import subprocess
        subprocess.run(
            ["systemctl", "stop", f"hostkit-{name}"],
            check=True,
            capture_output=True,
        )

        # Update status
        from hostkit.database import get_db
        get_db().update_project_status(name, "stopped")

        formatter.success(
            {"name": name, "service": f"hostkit-{name}"},
            f"Project '{name}' stopped",
        )

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)
    except subprocess.CalledProcessError as e:
        formatter.error(
            "SERVICE_STOP_FAILED",
            f"Failed to stop service: {e.stderr.decode() if e.stderr else 'unknown error'}",
        )


@project.command("restart")
@click.argument("name")
@click.pass_context
@project_access("name")
def restart(ctx: click.Context, name: str) -> None:
    """Restart a project's service.

    Example:
        hostkit project restart myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    try:
        # Verify project exists
        project_info = service.get_project(name)

        # Restart the service
        import subprocess
        subprocess.run(
            ["systemctl", "restart", f"hostkit-{name}"],
            check=True,
            capture_output=True,
        )

        # Update status
        from hostkit.database import get_db
        get_db().update_project_status(name, "running")

        formatter.success(
            {"name": name, "service": f"hostkit-{name}"},
            f"Project '{name}' restarted",
        )

    except ProjectServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)
    except subprocess.CalledProcessError as e:
        formatter.error(
            "SERVICE_RESTART_FAILED",
            f"Failed to restart service: {e.stderr.decode() if e.stderr else 'unknown error'}",
            f"Check logs with: journalctl -u hostkit-{name}",
        )


@project.command("regenerate-sudoers")
@click.argument("name", required=False)
@click.option(
    "--all", "all_projects",
    is_flag=True,
    help="Regenerate sudoers for all projects",
)
@click.pass_context
@root_only
def regenerate_sudoers(ctx: click.Context, name: str | None, all_projects: bool) -> None:
    """Regenerate sudoers rules for project(s).

    Updates the sudoers file with the latest project-scoped access rules.
    This is useful after upgrading HostKit to enable new project user permissions.

    Either provide a project NAME or use --all to update all projects.

    Examples:
        hostkit project regenerate-sudoers myapp
        hostkit project regenerate-sudoers --all
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ProjectService()

    if all_projects:
        results = service.regenerate_all_sudoers()

        success_count = sum(1 for r in results if r.get("success"))
        fail_count = len(results) - success_count

        formatter.success(
            {
                "projects": results,
                "total": len(results),
                "success": success_count,
                "failed": fail_count,
            },
            f"Regenerated sudoers for {success_count}/{len(results)} projects",
        )
    elif name:
        try:
            result = service.regenerate_sudoers(name)
            formatter.success(result, f"Sudoers regenerated for project '{name}'")
        except ProjectServiceError as e:
            formatter.error(e.code, e.message, e.suggestion)
    else:
        formatter.error(
            "MISSING_ARGUMENT",
            "Either provide a project name or use --all",
            "Usage: hostkit project regenerate-sudoers <name> or --all",
        )
