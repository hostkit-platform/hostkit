"""Deploy command for HostKit."""

import time
import click
from pathlib import Path

from hostkit.access import project_owner, get_access_context, AccessLevel
from hostkit.database import get_db
from hostkit.output import OutputFormatter
from hostkit.services.deploy_service import DeployService, DeployServiceError
from hostkit.services.alert_service import send_alert
from hostkit.services.auto_pause_service import AutoPauseService
from hostkit.services.event_service import EventService


@click.command("deploy")
@click.argument("project")
@click.option(
    "--env",
    "-e",
    "env_name",
    default=None,
    help="Environment to deploy to (e.g., staging, production)",
)
@click.option(
    "--source",
    "-s",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source directory to deploy (default: ./app)",
)
@click.option(
    "--git",
    "-g",
    "git_url",
    default=None,
    help="Git repository URL (or use configured repo if no URL given)",
)
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Git branch to checkout",
)
@click.option(
    "--tag",
    "-t",
    default=None,
    help="Git tag to checkout (overrides --branch)",
)
@click.option(
    "--commit",
    "-c",
    default=None,
    help="Git commit to checkout (overrides --branch and --tag)",
)
@click.option(
    "--install",
    "-i",
    is_flag=True,
    help="Install dependencies after sync",
)
@click.option(
    "--build",
    is_flag=True,
    help="Build the app before deploying (runs npm install && npm run build for Node/Next.js)",
)
@click.option(
    "--with-secrets",
    is_flag=True,
    help="Inject secrets from vault into .env",
)
@click.option(
    "--restart/--no-restart",
    default=True,
    help="Restart service after deploy (default: yes)",
)
@click.option(
    "--override-ratelimit",
    is_flag=True,
    help="Bypass rate limit checks (use with caution)",
)
@click.pass_context
@project_owner("project")
def deploy(
    ctx,
    project: str,
    env_name: str | None,
    source: Path | None,
    git_url: str | None,
    branch: str | None,
    tag: str | None,
    commit: str | None,
    install: bool,
    build: bool,
    with_secrets: bool,
    restart: bool,
    override_ratelimit: bool,
):
    """
    Deploy code to a project or environment.

    Syncs local code to the project's app directory on the VPS,
    optionally builds and installs dependencies, and restarts the service.

    Supports both local source and Git-based deployments.

    Use --env to deploy to a specific environment of a project.

    Use --build to build the app before deploying (runs npm install && npm run build
    for Node.js/Next.js projects). The build runs on the VPS in a temporary directory,
    and the built artifacts are then deployed.

    Examples:

        hostkit deploy myapp

        hostkit deploy myapp --source ./dist

        hostkit deploy myapp --env staging

        hostkit deploy myapp --env production --install

        hostkit deploy myapp --git https://github.com/user/repo.git

        hostkit deploy myapp --git https://github.com/user/repo.git --branch main

        hostkit deploy myapp --git --install   # Use configured repo

        hostkit deploy myapp --build --install  # Build and install deps

        hostkit deploy myapp --no-restart
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    # Resolve target (project or environment)
    deploy_target = project
    target_label = project

    if env_name:
        # Look up environment
        env = db.get_environment(project, env_name)
        if not env:
            formatter.error(
                code="ENVIRONMENT_NOT_FOUND",
                message=f"Environment '{env_name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit environment list {project}' to see available environments",
            )
            raise SystemExit(1)

        # Deploy to the environment's linux user (which acts like a project)
        deploy_target = env["linux_user"]
        target_label = f"{project}/{env_name}"

        # Check if environment user exists as a "project" in database
        # If not, we need to create a temporary project record
        env_project = db.get_project(deploy_target)
        if not env_project:
            # Get parent project for runtime info
            parent_project = db.get_project(project)
            if not parent_project:
                formatter.error(
                    code="PROJECT_NOT_FOUND",
                    message=f"Parent project '{project}' not found",
                )
                raise SystemExit(1)

            # Create a temporary project record for the environment
            # This allows the deploy service to work without modification
            db.create_project(
                name=deploy_target,
                runtime=parent_project["runtime"],
                port=env["port"],
                created_by="environment",
            )

    # Handle source vs git
    use_git = git_url is not None or branch is not None or tag is not None or commit is not None

    if use_git and source is not None:
        formatter.error(
            code="INVALID_OPTIONS",
            message="Cannot use --source with --git options",
            suggestion="Use either --source for local files or --git for repository",
        )
        raise SystemExit(1)

    # Default source to ./app if not using git
    if not use_git and source is None:
        source = Path("./app")
        if not source.exists():
            formatter.error(
                code="SOURCE_NOT_FOUND",
                message="Default source directory './app' does not exist on VPS",
                suggestion=(
                    "Specify a source with --source, use --git for repository, or deploy from local:\n"
                    f"  hostkit_deploy_local MCP tool for local machine deployments"
                ),
            )
            raise SystemExit(1)

    start_time = time.time()
    event_service = EventService()

    try:
        # Emit deploy started event
        try:
            event_service.deploy_started(
                project_name=project,
                source=str(source) if source else None,
                git_url=git_url,
                git_branch=branch or tag or commit,
            )
        except Exception:
            pass  # Events are non-blocking

        service = DeployService()

        if use_git:
            # Git-based deployment
            result = service.deploy_from_git(
                project=deploy_target,
                repo_url=git_url,
                branch=branch,
                tag=tag,
                commit=commit,
                build_app=build,
                install_deps=install,
                inject_secrets=with_secrets,
                restart=restart,
                override_ratelimit=override_ratelimit,
            )
        else:
            # Local source deployment
            result = service.deploy(
                project=deploy_target,
                source=source.resolve(),
                build_app=build,
                install_deps=install,
                inject_secrets=with_secrets,
                restart=restart,
                override_ratelimit=override_ratelimit,
            )

        data = {
            "project": project,
            "target": target_label,
            "files_synced": result.files_synced,
            "dependencies_installed": result.dependencies_installed,
            "secrets_injected": result.secrets_injected,
            "secrets_count": result.secrets_count,
            "service_restarted": result.service_restarted,
            "runtime": result.runtime,
        }
        if env_name:
            data["environment"] = env_name

        # Add build type if detected
        if hasattr(result, "build_type") and result.build_type:
            data["build_type"] = result.build_type

        # Add validation info
        if hasattr(result, "validation_passed"):
            data["validation_passed"] = result.validation_passed
            if result.validation_message:
                data["validation_message"] = result.validation_message

        # Add git info if present
        if hasattr(result, "git_info") and result.git_info:
            data["git"] = {
                "commit": result.git_info.commit[:8],
                "branch": result.git_info.branch,
                "tag": result.git_info.tag,
            }

        # Add build info if present
        if hasattr(result, "app_built") and result.app_built:
            data["app_built"] = True

        # Add iron-session info if present
        if hasattr(result, "iron_session_installed") and result.iron_session_installed:
            data["iron_session_installed"] = True

        # Build status message
        status_parts = [f"Deployed to {target_label}"]
        if hasattr(result, "build_type") and result.build_type:
            status_parts.append(f"({result.build_type})")
        if hasattr(result, "git_info") and result.git_info:
            status_parts.append(f"commit {result.git_info.commit[:8]}")
        if hasattr(result, "app_built") and result.app_built:
            status_parts.append("built")
        if result.dependencies_installed:
            status_parts.append("dependencies installed")
        if hasattr(result, "iron_session_installed") and result.iron_session_installed:
            status_parts.append("iron-session added")
        if result.secrets_injected:
            status_parts.append(f"{result.secrets_count} secrets injected")
        if result.service_restarted:
            status_parts.append("service restarted")

        # Show validation warning if failed
        if hasattr(result, "validation_passed") and not result.validation_passed:
            formatter.success(data, message=", ".join(status_parts))
            # Show validation warning
            click.echo()
            click.echo(click.style("WARNING: Post-deploy validation failed", fg="yellow", bold=True))
            if result.validation_message:
                click.echo(f"  {result.validation_message}")
            click.echo("  The service may not be running correctly.")
        else:
            formatter.success(data, message=", ".join(status_parts))

        # Emit deploy completed event
        try:
            duration = time.time() - start_time
            event_service.deploy_completed(
                project_name=project,
                files_synced=result.files_synced,
                duration_seconds=duration,
                release_name=getattr(result, "release_name", None),
            )
        except Exception:
            pass  # Events are non-blocking

        # Send success alert (non-blocking, errors are ignored)
        try:
            alert_data = {
                "files_synced": result.files_synced,
                "dependencies_installed": result.dependencies_installed,
                "service_restarted": result.service_restarted,
                "runtime": result.runtime,
                "release": getattr(result, "release_name", None),
            }
            if env_name:
                alert_data["environment"] = env_name
            if hasattr(result, "git_info") and result.git_info:
                alert_data["git_commit"] = result.git_info.commit[:8]
                alert_data["git_branch"] = result.git_info.branch
                alert_data["git_tag"] = result.git_info.tag
            send_alert(
                project_name=project,
                event_type="deploy",
                event_status="success",
                data=alert_data,
            )
        except Exception:
            pass  # Alerts are non-blocking

    except DeployServiceError as e:
        # Emit deploy failed event
        try:
            duration = time.time() - start_time
            event_service.deploy_failed(
                project_name=project,
                error=e.message,
                duration_seconds=duration,
            )
        except Exception:
            pass  # Events are non-blocking

        # Send failure alert
        try:
            send_alert(
                project_name=project,
                event_type="deploy",
                event_status="failure",
                data={"error": e.message, "code": e.code, "environment": env_name},
            )
        except Exception:
            pass  # Alerts are non-blocking

        # Check if we should auto-pause after this failure
        # Skip if error is already about being paused
        # Only auto-pause the main project, not environments
        if e.code != "PROJECT_PAUSED" and not env_name:
            try:
                auto_pause_service = AutoPauseService()
                if auto_pause_service.check_and_maybe_pause(project):
                    # Project was just paused
                    if not formatter.json_mode:
                        click.echo()
                        click.echo(click.style(
                            f"WARNING: Project '{project}' has been auto-paused due to repeated failures.",
                            fg="yellow", bold=True
                        ))
                        click.echo(f"Run 'hostkit resume {project}' to continue.")
            except Exception:
                pass  # Auto-pause check is non-blocking

        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)
    except Exception as e:
        # Emit deploy failed event
        try:
            duration = time.time() - start_time
            event_service.deploy_failed(
                project_name=project,
                error=str(e),
                duration_seconds=duration,
            )
        except Exception:
            pass  # Events are non-blocking

        # Send failure alert
        try:
            send_alert(
                project_name=project,
                event_type="deploy",
                event_status="failure",
                data={"error": str(e), "environment": env_name},
            )
        except Exception:
            pass  # Alerts are non-blocking

        # Check if we should auto-pause after this failure
        # Only auto-pause the main project, not environments
        if not env_name:
            try:
                auto_pause_service = AutoPauseService()
                if auto_pause_service.check_and_maybe_pause(project):
                    # Project was just paused
                    if not formatter.json_mode:
                        click.echo()
                        click.echo(click.style(
                            f"WARNING: Project '{project}' has been auto-paused due to repeated failures.",
                            fg="yellow", bold=True
                        ))
                        click.echo(f"Run 'hostkit resume {project}' to continue.")
            except Exception:
                pass  # Auto-pause check is non-blocking

        formatter.error(code="DEPLOY_FAILED", message=f"Deployment failed: {e}")
        raise SystemExit(1)
