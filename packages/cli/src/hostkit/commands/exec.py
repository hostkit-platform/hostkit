"""Execute arbitrary commands in project context for HostKit."""

import shlex
import subprocess

import click

from hostkit.access import project_access
from hostkit.database import get_db
from hostkit.output import OutputFormatter


@click.command("exec")
@click.argument("project")
@click.argument("command", nargs=-1, required=True)
@click.option(
    "--workdir",
    "-w",
    default=None,
    help="Working directory (default: project's app directory)",
)
@click.option(
    "--env-file/--no-env-file",
    default=True,
    help="Source the project's .env file (default: yes)",
)
@click.option(
    "--timeout",
    "-t",
    default=300,
    help="Command timeout in seconds (default: 300)",
)
@click.pass_context
@project_access("project")
def exec_cmd(
    ctx: click.Context,
    project: str,
    command: tuple[str, ...],
    workdir: str | None,
    env_file: bool,
    timeout: int,
) -> None:
    """Execute a command in a project's context.

    Runs the command as the project user, in the project's app directory,
    with the project's environment variables loaded.

    This is useful for:
    - Running one-off scripts (e.g., database seeders, dev utilities)
    - Executing npm/npx commands
    - Running Python scripts in the project's venv
    - Any administrative task that needs project context

    Examples:
        hostkit exec myapp npx prisma migrate deploy
        hostkit exec myapp npm run seed
        hostkit exec myapp python scripts/cleanup.py
        hostkit exec myapp -- node -e "console.log('hello')"

    Note: Use -- before the command if it contains flags that might
    be interpreted by hostkit.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    db = get_db()

    # Verify project exists
    project_info = db.get_project(project)
    if not project_info:
        formatter.error(
            code="PROJECT_NOT_FOUND",
            message=f"Project '{project}' does not exist",
            suggestion="Use 'hostkit project list' to see available projects",
        )
        raise SystemExit(1)

    # Build the command string
    cmd_str = " ".join(shlex.quote(c) for c in command)

    # Determine working directory
    if workdir is None:
        workdir = f"/home/{project}/app"

    # Build environment setup
    env_setup = ""
    if env_file:
        env_path = f"/home/{project}/.env"
        env_setup = f"set -a && source {env_path} 2>/dev/null; set +a; "

    # Handle runtime-specific PATH adjustments
    runtime = project_info.get("runtime", "python")
    path_setup = ""
    if runtime == "python":
        # Add venv to PATH for Python projects
        path_setup = f'export PATH="/home/{project}/venv/bin:$PATH"; '
    elif runtime in ("node", "nextjs"):
        # Add node_modules/.bin to PATH for Node projects
        path_setup = f'export PATH="/home/{project}/app/node_modules/.bin:$PATH"; '

    # Build full command to run as project user
    full_cmd = f"cd {shlex.quote(workdir)} && {env_setup}{path_setup}{cmd_str}"

    # Run as project user using sudo
    sudo_cmd = ["sudo", "-u", project, "bash", "-c", full_cmd]

    if ctx.obj["json_mode"]:
        # JSON mode: capture output and return structured response
        try:
            result = subprocess.run(
                sudo_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            formatter.success(
                message=f"Command executed in project '{project}'",
                data={
                    "project": project,
                    "command": cmd_str,
                    "workdir": workdir,
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "success": result.returncode == 0,
                },
            )

            if result.returncode != 0:
                raise SystemExit(result.returncode)

        except subprocess.TimeoutExpired:
            formatter.error(
                code="COMMAND_TIMEOUT",
                message=f"Command timed out after {timeout} seconds",
                suggestion="Use --timeout to increase the limit",
            )
            raise SystemExit(1)

    else:
        # Interactive mode: stream output directly
        click.echo(f"Executing in {project}@{workdir}:")
        click.echo(f"  $ {cmd_str}")
        click.echo("-" * 60)

        try:
            result = subprocess.run(
                sudo_cmd,
                timeout=timeout,
            )

            click.echo("-" * 60)
            if result.returncode == 0:
                click.echo(click.style("Command completed successfully", fg="green"))
            else:
                click.echo(
                    click.style(f"Command failed with exit code {result.returncode}", fg="red")
                )
                raise SystemExit(result.returncode)

        except subprocess.TimeoutExpired:
            click.echo("-" * 60)
            click.echo(click.style(f"Command timed out after {timeout} seconds", fg="red"))
            raise SystemExit(1)
