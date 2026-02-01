"""Rollback command for HostKit projects."""

import click

from hostkit.access import project_owner
from hostkit.services.release_service import ReleaseService, ReleaseServiceError
from hostkit.services.service_service import ServiceError, ServiceService


@click.command("rollback")
@click.argument("project")
@click.option(
    "--list",
    "list_releases",
    is_flag=True,
    help="List available releases instead of rolling back",
)
@click.option(
    "--to",
    "target_release",
    default=None,
    help="Roll back to a specific release by name",
)
@click.option(
    "--full",
    is_flag=True,
    help="Full rollback: code + database + environment variables",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be rolled back without making changes",
)
@click.pass_context
@project_owner("project")
def rollback(
    ctx: click.Context,
    project: str,
    list_releases: bool,
    target_release: str | None,
    full: bool,
    dry_run: bool,
) -> None:
    """Roll back a project to a previous release.

    Without options, rolls back to the immediately previous release (code only).
    Use --full to also restore the database and environment variables.
    Use --dry-run to preview changes without executing them.

    Examples:
        hostkit rollback myapp                    # Roll back code only
        hostkit rollback myapp --full             # Roll back code + DB + env
        hostkit rollback myapp --full --dry-run   # Preview full rollback
        hostkit rollback myapp --list             # List releases
        hostkit rollback myapp --to 20251213-143022  # Roll back to specific
    """
    formatter = ctx.obj.get("formatter")
    json_mode = ctx.obj.get("json_mode", False)

    release_service = ReleaseService()
    service_service = ServiceService()

    try:
        # Check if project uses releases
        if not release_service.is_release_based(project):
            message = f"Project '{project}' does not use release-based deployments"
            if formatter and json_mode:
                formatter.error(
                    code="NOT_RELEASE_BASED",
                    message=message,
                    suggestion="Deploy at least once to enable release-based deployments",
                )
            raise click.ClickException(
                f"{message}. Deploy at least once to enable release-based deployments."
            )

        # List mode
        if list_releases:
            releases = release_service.list_releases(project, limit=20)

            if json_mode and formatter:
                formatter.success(
                    data={
                        "project": project,
                        "releases": [
                            {
                                "name": r.release_name,
                                "deployed_at": r.deployed_at,
                                "is_current": r.is_current,
                                "files_synced": r.files_synced,
                                "deployed_by": r.deployed_by,
                                "checkpoint_id": r.checkpoint_id,
                                "has_env_snapshot": r.env_snapshot is not None,
                            }
                            for r in releases
                        ],
                    }
                )
                return

            if not releases:
                click.echo(f"No releases found for project '{project}'")
                return

            click.echo(f"\nReleases for '{project}':\n")
            click.echo(f"{'NAME':<20} {'DEPLOYED AT':<25} {'FILES':<8} {'SNAPSHOT':<10} {'STATUS'}")
            click.echo("-" * 85)

            for release in releases:
                status = click.style("CURRENT", fg="green", bold=True) if release.is_current else ""
                files = str(release.files_synced) if release.files_synced else "-"
                # Show snapshot status
                snapshot_status = ""
                if release.checkpoint_id and release.env_snapshot:
                    snapshot_status = "DB+ENV"
                elif release.checkpoint_id:
                    snapshot_status = "DB"
                elif release.env_snapshot:
                    snapshot_status = "ENV"
                else:
                    snapshot_status = "-"
                row = (
                    f"{release.release_name:<20} "
                    f"{release.deployed_at:<25} "
                    f"{files:<8} {snapshot_status:<10} "
                    f"{status}"
                )
                click.echo(row)

            click.echo(f"\nTotal: {len(releases)} release(s)")
            click.echo(
                click.style(
                    "\nTo roll back: hostkit rollback <project> --to <release-name>",
                    fg="yellow",
                )
            )
            click.echo(
                click.style(
                    "For full rollback (code + DB + env): hostkit rollback <project> --full",
                    fg="yellow",
                )
            )
            return

        # Get current release
        current = release_service.get_current_release(project)
        if not current:
            message = f"No current release found for project '{project}'"
            if formatter and json_mode:
                formatter.error(code="NO_CURRENT_RELEASE", message=message)
            raise click.ClickException(message)

        # Determine target release
        if target_release:
            # Roll back to specific release
            try:
                target = release_service.get_release(project, target_release)
            except ReleaseServiceError as e:
                if formatter and json_mode:
                    formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
                raise click.ClickException(e.message)

            if target.release_name == current.release_name:
                message = f"Release '{target_release}' is already the current release"
                if formatter and json_mode:
                    formatter.error(code="ALREADY_CURRENT", message=message)
                raise click.ClickException(message)
        else:
            # Roll back to previous release
            target = release_service.get_previous_release(project)
            if not target:
                message = f"No previous release available for project '{project}'"
                if formatter and json_mode:
                    formatter.error(
                        code="NO_PREVIOUS_RELEASE",
                        message=message,
                        suggestion="Use --list to see available releases",
                    )
                raise click.ClickException(f"{message}. Use --list to see available releases.")

        # Dry-run mode: show what would happen
        if dry_run:
            _show_rollback_preview(project, current, target, full, formatter, json_mode)
            return

        # Perform rollback
        if not json_mode:
            click.echo(f"Rolling back '{project}'...")
            click.echo(f"  Current release: {current.release_name}")
            click.echo(f"  Target release:  {target.release_name}")
            if full:
                click.echo("  Mode: Full rollback (code + database + environment)")
            else:
                click.echo("  Mode: Code only")

        # Full rollback: restore database checkpoint first
        db_restored = False
        db_restore_error = None
        if full and target.checkpoint_id:
            try:
                from hostkit.services.checkpoint_service import CheckpointService

                checkpoint_service = CheckpointService()
                checkpoint_service.restore_checkpoint(
                    project_name=project,
                    checkpoint_id=target.checkpoint_id,
                    create_pre_restore=True,
                )
                db_restored = True
                if not json_mode:
                    click.echo(
                        click.style(
                            f"  ✓ Database restored from checkpoint {target.checkpoint_id}",
                            fg="green",
                        )
                    )
            except Exception as e:
                db_restore_error = str(e)
                if not json_mode:
                    click.echo(click.style(f"  ⚠ Database restore failed: {e}", fg="yellow"))

        # Full rollback: restore environment variables
        env_restored = False
        env_restore_error = None
        if full and target.env_snapshot:
            try:
                from hostkit.services.env_service import EnvService

                env_service = EnvService()
                env_service.restore_snapshot(project, target.env_snapshot)
                env_restored = True
                if not json_mode:
                    click.echo(click.style("  ✓ Environment variables restored", fg="green"))
            except Exception as e:
                env_restore_error = str(e)
                if not json_mode:
                    click.echo(click.style(f"  ⚠ Environment restore failed: {e}", fg="yellow"))

        # Activate the target release (updates symlink)
        activated = release_service.activate_release(project, target.release_name)

        # Restart the service
        restart_result = None
        restart_error = None
        try:
            restart_result = service_service.restart(project)
        except ServiceError as e:
            restart_error = e.message

        # Output result
        if json_mode and formatter:
            formatter.success(
                data={
                    "project": project,
                    "previous_release": current.release_name,
                    "current_release": activated.release_name,
                    "full_rollback": full,
                    "database_restored": db_restored,
                    "database_restore_error": db_restore_error,
                    "env_restored": env_restored,
                    "env_restore_error": env_restore_error,
                    "service_restarted": restart_result is not None,
                    "restart_error": restart_error,
                },
                message=f"Rolled back to release '{activated.release_name}'",
            )
        else:
            click.echo()
            click.echo(
                click.style(
                    f"✓ Rolled back to release '{activated.release_name}'", fg="green", bold=True
                )
            )
            if restart_result:
                click.echo(click.style("✓ Service restarted", fg="green"))
            elif restart_error:
                click.echo(click.style(f"⚠ Service restart failed: {restart_error}", fg="yellow"))
            click.echo()
            click.echo("Tip: Check health with 'hostkit health " + project + "'")

    except ReleaseServiceError as e:
        if formatter and json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)


def _show_rollback_preview(
    project: str,
    current,
    target,
    full: bool,
    formatter,
    json_mode: bool,
) -> None:
    """Show a preview of what would be rolled back."""

    # Collect preview data
    preview = {
        "project": project,
        "current_release": current.release_name,
        "target_release": target.release_name,
        "full_rollback": full,
        "code_rollback": True,
        "database_rollback": False,
        "env_rollback": False,
        "env_changes": None,
    }

    # Check database checkpoint
    if full and target.checkpoint_id:
        preview["database_rollback"] = True
        preview["checkpoint_id"] = target.checkpoint_id
        try:
            from hostkit.services.checkpoint_service import CheckpointService

            checkpoint_service = CheckpointService()
            checkpoint = checkpoint_service.get_checkpoint(target.checkpoint_id)
            preview["checkpoint_info"] = {
                "id": checkpoint.id,
                "label": checkpoint.label,
                "created_at": checkpoint.created_at,
                "size_bytes": checkpoint.size_bytes,
            }
        except Exception:
            preview["checkpoint_info"] = None

    # Check environment snapshot and compute diff
    if full and target.env_snapshot:
        preview["env_rollback"] = True
        try:
            from hostkit.services.env_service import EnvService

            env_service = EnvService()
            env_diff = env_service.compare_snapshot(project, target.env_snapshot)
            preview["env_changes"] = env_diff
        except Exception as e:
            preview["env_changes"] = {"error": str(e)}

    if json_mode and formatter:
        formatter.success(
            data=preview,
            message="Dry run - no changes made",
        )
        return

    # Pretty print preview
    click.echo()
    click.echo(click.style("=== ROLLBACK PREVIEW (dry-run) ===", fg="cyan", bold=True))
    click.echo()
    click.echo(f"Project: {project}")
    click.echo(f"Current release: {current.release_name}")
    click.echo(f"Target release:  {target.release_name}")
    click.echo(f"Mode: {'Full rollback' if full else 'Code only'}")
    click.echo()

    # Code rollback
    click.echo(click.style("Code:", fg="white", bold=True))
    click.echo(f"  Will switch symlink to {target.release_name}")
    click.echo()

    # Database rollback
    if full:
        click.echo(click.style("Database:", fg="white", bold=True))
        if target.checkpoint_id:
            click.echo(f"  Will restore from checkpoint {target.checkpoint_id}")
            if preview.get("checkpoint_info"):
                cp = preview["checkpoint_info"]
                click.echo(f"  Checkpoint label: {cp.get('label') or '(none)'}")
                click.echo(f"  Created: {cp.get('created_at')}")
                size_mb = (cp.get("size_bytes", 0) or 0) / (1024 * 1024)
                click.echo(f"  Size: {size_mb:.2f} MB")
        else:
            click.echo(
                click.style("  No database checkpoint available for this release", fg="yellow")
            )
        click.echo()

        # Environment rollback
        click.echo(click.style("Environment Variables:", fg="white", bold=True))
        if target.env_snapshot:
            env_changes = preview.get("env_changes", {})
            if env_changes.get("error"):
                click.echo(
                    click.style(f"  Error checking changes: {env_changes['error']}", fg="red")
                )
            elif env_changes.get("has_changes"):
                if env_changes.get("added"):
                    added = ", ".join(env_changes["added"])
                    click.echo(f"  Variables to remove (added since snapshot): {added}")
                if env_changes.get("removed"):
                    click.echo(f"  Variables to restore: {', '.join(env_changes['removed'])}")
                if env_changes.get("changed"):
                    changed_keys = [c["key"] for c in env_changes["changed"]]
                    click.echo(f"  Variables with changed values: {', '.join(changed_keys)}")
            else:
                click.echo("  No changes detected (environment already matches snapshot)")
        else:
            click.echo(
                click.style("  No environment snapshot available for this release", fg="yellow")
            )
        click.echo()

    click.echo(click.style("No changes have been made. Remove --dry-run to execute.", fg="cyan"))
    click.echo()
