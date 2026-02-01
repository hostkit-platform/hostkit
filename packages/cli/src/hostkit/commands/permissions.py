"""Permissions management command for HostKit CLI.

Provides tools for managing and fixing sudoers permissions,
detecting permission gaps, and syncing permissions from templates.
"""

import subprocess
from pathlib import Path
from typing import Any

import click

from hostkit.access import COMMAND_SCOPES, CommandScope, root_only
from hostkit.database import get_db
from hostkit.output import OutputFormatter


@click.group("permissions")
@click.pass_context
def permissions(ctx: click.Context) -> None:
    """Manage sudoers and access control.

    Tools for managing project permissions, detecting gaps,
    and syncing sudoers configuration from templates.

    Examples:

        hostkit permissions gaps              # Detect missing entries
        hostkit permissions show myapp        # Show project permissions
        hostkit permissions sync --all        # Sync all projects
        hostkit permissions sync myapp        # Sync specific project
    """
    pass


@permissions.command("gaps")
@root_only
@click.pass_context
def gaps(ctx: click.Context) -> None:
    """Detect missing sudoers entries.

    Compares COMMAND_SCOPES definitions against actual sudoers
    files to identify commands that should be allowed but aren't
    configured in the sudoers template.

    This helps identify permission issues before they cause
    "sudo: not allowed" errors at runtime.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    projects = db.list_projects()
    if not projects:
        formatter.success({"gaps": [], "projects_checked": 0}, "No projects to check")
        return

    all_gaps: list[dict[str, Any]] = []

    for project in projects:
        project_name = project["name"]
        sudoers_file = Path(f"/etc/sudoers.d/hostkit-{project_name}")

        if not sudoers_file.exists():
            all_gaps.append(
                {
                    "project": project_name,
                    "type": "missing_file",
                    "message": f"Sudoers file not found: {sudoers_file}",
                    "suggestion": f"hostkit permissions sync {project_name}",
                }
            )
            continue

        # Read current sudoers content
        try:
            content = sudoers_file.read_text()
        except PermissionError:
            all_gaps.append(
                {
                    "project": project_name,
                    "type": "permission_error",
                    "message": f"Cannot read sudoers file: {sudoers_file}",
                    "suggestion": "Run as root",
                }
            )
            continue

        # Check for expected commands based on COMMAND_SCOPES
        project_gaps = _check_project_permissions(project_name, content)
        all_gaps.extend(project_gaps)

    if all_gaps:
        formatter.success(
            {
                "gaps": all_gaps,
                "projects_checked": len(projects),
                "total_gaps": len(all_gaps),
            },
            f"Found {len(all_gaps)} permission gap(s)",
        )
    else:
        formatter.success(
            {
                "gaps": [],
                "projects_checked": len(projects),
                "total_gaps": 0,
            },
            "No permission gaps detected",
        )


def _check_project_permissions(project_name: str, sudoers_content: str) -> list[dict[str, Any]]:
    """Check a project's sudoers file for missing commands."""
    gaps = []

    # Commands that PROJECT_SCOPED or PROJECT_READ should allow
    expected_patterns = []
    for cmd, scope in COMMAND_SCOPES.items():
        if scope in (CommandScope.PROJECT_SCOPED, CommandScope.PROJECT_READ):
            # Build expected sudoers pattern
            parts = cmd.split()
            if len(parts) >= 1:
                # e.g., "deploy" -> "/usr/local/bin/hostkit deploy {project}"
                pattern = f"/usr/local/bin/hostkit {cmd.replace(' ', ' ')}"
                expected_patterns.append((cmd, pattern))

    for cmd, pattern in expected_patterns:
        # Check if pattern exists in sudoers (with project name substituted)
        check_pattern = pattern.replace("{project}", project_name)
        if check_pattern not in sudoers_content and f"hostkit {cmd}" not in sudoers_content:
            gaps.append(
                {
                    "project": project_name,
                    "type": "missing_command",
                    "command": cmd,
                    "message": f"Command '{cmd}' not found in sudoers",
                    "suggestion": (
                        f"Add to sudoers.j2 or run: hostkit permissions sync {project_name}"
                    ),
                }
            )

    return gaps


@permissions.command("show")
@click.argument("project")
@root_only
@click.pass_context
def show(ctx: click.Context, project: str) -> None:
    """Show current permissions for a project.

    Displays all sudoers entries configured for the specified
    project, organized by category (services, deploy, etc.).
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    # Verify project exists
    project_info = db.get_project(project)
    if not project_info:
        formatter.error(
            code="PROJECT_NOT_FOUND",
            message=f"Project '{project}' does not exist",
            suggestion="Run 'hostkit project list' to see available projects",
        )
        return

    sudoers_file = Path(f"/etc/sudoers.d/hostkit-{project}")
    if not sudoers_file.exists():
        formatter.error(
            code="SUDOERS_NOT_FOUND",
            message=f"Sudoers file not found: {sudoers_file}",
            suggestion=f"Run 'hostkit permissions sync {project}' to create it",
        )
        return

    try:
        content = sudoers_file.read_text()
    except PermissionError:
        formatter.error(
            code="PERMISSION_DENIED",
            message=f"Cannot read sudoers file: {sudoers_file}",
            suggestion="Run as root",
        )
        return

    # Parse sudoers content into categories
    permissions_data = _parse_sudoers(content, project)

    formatter.success(
        {
            "project": project,
            "sudoers_file": str(sudoers_file),
            "permissions": permissions_data,
            "total_rules": sum(len(rules) for rules in permissions_data.values()),
        },
        f"Permissions for project '{project}'",
    )


def _parse_sudoers(content: str, project: str) -> dict[str, list[str]]:
    """Parse sudoers content into categories."""
    categories: dict[str, list[str]] = {
        "services": [],
        "deploy": [],
        "database": [],
        "auth": [],
        "payments": [],
        "sms": [],
        "voice": [],
        "booking": [],
        "chatbot": [],
        "vector": [],
        "r2": [],
        "mail": [],
        "backup": [],
        "other": [],
    }

    current_category = "other"

    for line in content.split("\n"):
        line = line.strip()

        # Skip empty lines and comments (but track section comments)
        if not line:
            continue

        if line.startswith("#"):
            # Check for section headers
            lower = line.lower()
            if "service management" in lower or "systemd" in lower:
                current_category = "services"
            elif "deploy" in lower or "rollback" in lower:
                current_category = "deploy"
            elif "database" in lower:
                current_category = "database"
            elif "auth" in lower:
                current_category = "auth"
            elif "payment" in lower:
                current_category = "payments"
            elif "sms" in lower:
                current_category = "sms"
            elif "voice" in lower:
                current_category = "voice"
            elif "booking" in lower:
                current_category = "booking"
            elif "chatbot" in lower:
                current_category = "chatbot"
            elif "vector" in lower or "rag" in lower:
                current_category = "vector"
            elif "r2 storage" in lower:
                current_category = "r2"
            elif "mail" in lower:
                current_category = "mail"
            elif "backup" in lower:
                current_category = "backup"
            continue

        # Extract the command part from sudoers rule
        if "NOPASSWD:" in line:
            parts = line.split("NOPASSWD:")
            if len(parts) > 1:
                cmd = parts[1].strip()
                categories[current_category].append(cmd)

    return categories


@permissions.command("sync")
@click.argument("project", required=False)
@click.option("--all", "sync_all", is_flag=True, help="Sync all projects")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
@root_only
@click.pass_context
def sync(ctx: click.Context, project: str | None, sync_all: bool, dry_run: bool) -> None:
    """Regenerate sudoers from template.

    Updates the sudoers file for a project (or all projects) using
    the current sudoers.j2 template. This ensures all new commands
    added to the template are reflected in the actual permissions.

    Examples:

        hostkit permissions sync myapp        # Sync one project
        hostkit permissions sync --all        # Sync all projects
        hostkit permissions sync myapp --dry-run  # Preview changes
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    if not project and not sync_all:
        formatter.error(
            code="ARGUMENT_REQUIRED",
            message="Specify a project name or use --all",
            suggestion="hostkit permissions sync myapp  OR  hostkit permissions sync --all",
        )
        return

    if project and sync_all:
        formatter.error(
            code="CONFLICTING_OPTIONS",
            message="Cannot specify both PROJECT and --all",
            suggestion=(
                "Use either: hostkit permissions sync myapp  OR  hostkit permissions sync --all"
            ),
        )
        return

    # Get projects to sync
    if sync_all:
        projects = db.list_projects()
        project_names = [p["name"] for p in projects]
    else:
        # Verify project exists
        project_info = db.get_project(project)
        if not project_info:
            formatter.error(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
            return
        project_names = [project]

    results = []
    for proj_name in project_names:
        result = _sync_project_permissions(proj_name, dry_run)
        results.append(result)

    success_count = sum(1 for r in results if r["success"])
    failed_count = len(results) - success_count

    if dry_run:
        formatter.success(
            {
                "dry_run": True,
                "projects": results,
                "would_sync": len(project_names),
            },
            f"Dry run: would sync {len(project_names)} project(s)",
        )
    else:
        formatter.success(
            {
                "projects": results,
                "synced": success_count,
                "failed": failed_count,
            },
            f"Synced {success_count} of {len(project_names)} project(s)",
        )


def _sync_project_permissions(project_name: str, dry_run: bool) -> dict[str, Any]:
    """Sync permissions for a single project."""
    from jinja2 import Environment, FileSystemLoader

    template_dir = Path("/var/lib/hostkit/templates")
    template_file = template_dir / "sudoers.j2"
    output_file = Path(f"/etc/sudoers.d/hostkit-{project_name}")

    result = {
        "project": project_name,
        "success": False,
        "message": "",
        "output_file": str(output_file),
    }

    if not template_file.exists():
        result["message"] = f"Template not found: {template_file}"
        return result

    try:
        # Load and render template
        env = Environment(loader=FileSystemLoader(str(template_dir)))
        template = env.get_template("sudoers.j2")
        content = template.render(project_name=project_name)

        if dry_run:
            result["success"] = True
            result["message"] = "Would regenerate sudoers file"
            result["content_lines"] = len(content.split("\n"))
            return result

        # Write to temp file first
        temp_file = output_file.with_suffix(".tmp")
        temp_file.write_text(content)

        # Validate with visudo
        validate_result = subprocess.run(
            ["visudo", "-c", "-f", str(temp_file)],
            capture_output=True,
            text=True,
        )

        if validate_result.returncode != 0:
            temp_file.unlink()
            result["message"] = f"Validation failed: {validate_result.stderr}"
            return result

        # Move temp file to final location
        temp_file.rename(output_file)
        output_file.chmod(0o440)

        result["success"] = True
        result["message"] = "Sudoers file regenerated"

    except Exception as e:
        result["message"] = f"Error: {e}"

    return result


@permissions.command("verify")
@click.argument("project")
@click.argument("command")
@root_only
@click.pass_context
def verify(ctx: click.Context, project: str, command: str) -> None:
    """Verify if a command is allowed for a project user.

    Tests whether a specific command can be run by the project user
    via sudo. Useful for debugging permission issues.

    Examples:

        hostkit permissions verify myapp "deploy myapp"
        hostkit permissions verify myapp "chatbot status myapp"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    # Use sudo -l -U to check what's allowed
    try:
        result = subprocess.run(
            ["sudo", "-l", "-U", project],
            capture_output=True,
            text=True,
            timeout=5,
        )

        allowed_commands = result.stdout

        # Build the full command to check
        full_cmd = f"/usr/local/bin/hostkit {command}"

        # Check if command appears in allowed list
        is_allowed = (
            full_cmd in allowed_commands or f"hostkit {command.split()[0]}" in allowed_commands
        )

        # Also check for wildcard patterns
        parts = command.split()
        if len(parts) > 0:
            base_pattern = f"/usr/local/bin/hostkit {parts[0]}"
            if f"{base_pattern} *" in allowed_commands:
                is_allowed = True

        formatter.success(
            {
                "project": project,
                "command": command,
                "full_command": full_cmd,
                "allowed": is_allowed,
                "suggestion": None
                if is_allowed
                else f"Add to sudoers or run: hostkit permissions sync {project}",
            },
            f"Command {'allowed' if is_allowed else 'NOT allowed'} for {project}",
        )

    except subprocess.TimeoutExpired:
        formatter.error(
            code="TIMEOUT",
            message="Timed out checking sudo permissions",
            suggestion="Check that sudo is working correctly",
        )
    except Exception as e:
        formatter.error(
            code="CHECK_FAILED",
            message=f"Failed to verify permissions: {e}",
            suggestion="Ensure you are running as root",
        )
