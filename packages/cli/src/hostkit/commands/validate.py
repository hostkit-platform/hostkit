"""Pre-deployment validation command for HostKit projects."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from hostkit.access import project_access
from hostkit.database import get_db
from hostkit.output import OutputFormatter


@dataclass
class ValidationIssue:
    """A single validation issue."""

    category: str  # entrypoint, dependencies, env, database, port
    severity: str  # error, warning, info
    message: str
    suggestion: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "details": self.details if self.details else None,
        }


@dataclass
class ValidationResult:
    """Complete validation result for a project."""

    project: str
    valid: bool  # True if no errors (warnings OK)
    issues: list[ValidationIssue] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)
    runtime: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "valid": self.valid,
            "runtime": self.runtime,
            "checks_passed": self.checks_passed,
            "issues": [i.to_dict() for i in self.issues],
            "error_count": sum(1 for i in self.issues if i.severity == "error"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
        }


def _get_project_home(project_name: str) -> Path:
    """Get the home directory for a project."""
    return Path(f"/home/{project_name}")


def _check_entrypoint(project_name: str, runtime: str, home: Path) -> list[ValidationIssue]:
    """Check that the app entrypoint exists."""
    issues = []
    app_path = home / "app"

    if not app_path.exists():
        issues.append(
            ValidationIssue(
                category="entrypoint",
                severity="error",
                message="App directory not found",
                suggestion="Deploy code first with 'hostkit deploy'",
                details={"expected": str(app_path)},
            )
        )
        return issues

    # Check based on runtime
    if runtime == "python":
        # Python: check for app/__init__.py or app/__main__.py
        main_file = app_path / "__main__.py"
        init_file = app_path / "__init__.py"
        main_py = app_path / "main.py"

        if not (main_file.exists() or (init_file.exists()) or main_py.exists()):
            issues.append(
                ValidationIssue(
                    category="entrypoint",
                    severity="error",
                    message="Python entrypoint not found",
                    suggestion="Create app/__main__.py, app/__init__.py, or app/main.py",
                    details={"checked": ["app/__main__.py", "app/__init__.py", "app/main.py"]},
                )
            )

    elif runtime == "node":
        # Node: check for app/index.js or package.json with main
        index_js = app_path / "index.js"
        package_json = app_path / "package.json"

        if not index_js.exists() and not package_json.exists():
            issues.append(
                ValidationIssue(
                    category="entrypoint",
                    severity="error",
                    message="Node entrypoint not found",
                    suggestion="Create app/index.js or package.json with 'main' field",
                    details={"checked": ["app/index.js", "app/package.json"]},
                )
            )

    elif runtime == "nextjs":
        # Next.js: check for package.json OR server.js (standalone)
        package_json = app_path / "package.json"
        server_js = app_path / "server.js"

        # Standalone deployments have server.js at root
        if server_js.exists():
            # This is a standalone deployment - valid entrypoint
            pass
        elif not package_json.exists():
            issues.append(
                ValidationIssue(
                    category="entrypoint",
                    severity="error",
                    message="Next.js entrypoint not found",
                    suggestion="Ensure package.json (standard) or server.js (standalone) exists",
                    details={"checked": ["app/package.json", "app/server.js"]},
                )
            )

    elif runtime == "static":
        # Static: check for index.html
        index_html = app_path / "index.html"

        if not index_html.exists():
            issues.append(
                ValidationIssue(
                    category="entrypoint",
                    severity="warning",
                    message="index.html not found (may be OK for SPA)",
                    suggestion="Ensure index.html exists or nginx is configured for SPA",
                    details={"checked": ["app/index.html"]},
                )
            )

    return issues


def _check_dependencies(project_name: str, runtime: str, home: Path) -> list[ValidationIssue]:
    """Check that dependencies are installed."""
    issues = []
    app_path = home / "app"

    if runtime == "python":
        # Check for requirements.txt and venv
        requirements = app_path / "requirements.txt"
        venv = home / "venv"

        if requirements.exists():
            if not venv.exists():
                issues.append(
                    ValidationIssue(
                        category="dependencies",
                        severity="error",
                        message="Virtual environment not found",
                        suggestion=(
                            "Run 'hostkit deploy --install' to create venv and install dependencies"
                        ),
                        details={"expected": str(venv)},
                    )
                )
            else:
                # Check if requirements are installed
                site_packages = list(venv.glob("lib/python*/site-packages"))
                if not site_packages:
                    issues.append(
                        ValidationIssue(
                            category="dependencies",
                            severity="error",
                            message="Site-packages not found in venv",
                            suggestion="Run 'hostkit deploy --install' to reinstall dependencies",
                        )
                    )

    elif runtime in ("node", "nextjs"):
        # Check for package.json and node_modules
        package_json = app_path / "package.json"
        node_modules = app_path / "node_modules"
        server_js = app_path / "server.js"

        # For Next.js standalone, node_modules should be bundled in the deploy
        is_standalone = runtime == "nextjs" and server_js.exists()

        if package_json.exists() or is_standalone:
            if not node_modules.exists():
                if is_standalone:
                    # Standalone-specific error message
                    issues.append(
                        ValidationIssue(
                            category="dependencies",
                            severity="error",
                            message="Standalone node_modules not found",
                            suggestion=(
                                "Next.js standalone builds must include bundled node_modules. "
                                "Ensure the standalone output includes node_modules when deploying."
                            ),
                            details={"expected": str(node_modules), "build_type": "standalone"},
                        )
                    )
                else:
                    issues.append(
                        ValidationIssue(
                            category="dependencies",
                            severity="error",
                            message="node_modules not found",
                            suggestion="Run 'hostkit deploy --install' to install npm dependencies",
                            details={"expected": str(node_modules)},
                        )
                    )

    return issues


def _check_env_vars(
    project_name: str, runtime: str, home: Path, services: list[str]
) -> list[ValidationIssue]:
    """Check that required environment variables are set."""
    issues = []
    env_file = home / ".env"

    if not env_file.exists():
        issues.append(
            ValidationIssue(
                category="env",
                severity="error",
                message=".env file not found",
                suggestion="Create .env file with required variables",
                details={"expected": str(env_file)},
            )
        )
        return issues

    # Parse .env file
    env_vars = {}
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()
    except PermissionError:
        issues.append(
            ValidationIssue(
                category="env",
                severity="warning",
                message="Cannot read .env file (permission denied)",
                suggestion="Check file permissions",
            )
        )
        return issues

    # Required for all projects
    required = ["PORT"]

    # Required based on runtime
    # Note: HOSTNAME is typically set at runtime for Next.js, NODE_ENV defaults to production
    # So we don't require these in .env

    # Required based on services
    if "auth" in services:
        required.append("AUTH_URL")

    # Recommended (warning level) based on services
    recommended = []

    if "payments" in services:
        recommended.extend(["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"])

    if "sms" in services:
        recommended.extend(["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"])

    if "booking" in services:
        recommended.append("BOOKING_URL")

    if "chatbot" in services:
        recommended.append("CHATBOT_URL")

    # Check required vars (error level)
    missing_required = []
    for var in required:
        if var not in env_vars:
            missing_required.append(var)

    if missing_required:
        issues.append(
            ValidationIssue(
                category="env",
                severity="error",
                message="Missing required environment variables",
                suggestion=f"Set these in .env: {', '.join(missing_required)}",
                details={"missing": missing_required},
            )
        )

    # Check recommended vars (warning level)
    missing_recommended = []
    for var in recommended:
        if var not in env_vars:
            missing_recommended.append(var)

    if missing_recommended:
        issues.append(
            ValidationIssue(
                category="env",
                severity="warning",
                message="Missing recommended environment variables for enabled services",
                suggestion=f"Consider setting in .env: {', '.join(missing_recommended)}",
                details={"missing": missing_recommended},
            )
        )

    # Check for common issues
    if "DATABASE_URL" in env_vars:
        db_url = env_vars["DATABASE_URL"]
        if "localhost" in db_url and "127.0.0.1" not in db_url:
            issues.append(
                ValidationIssue(
                    category="env",
                    severity="warning",
                    message="DATABASE_URL uses 'localhost' instead of '127.0.0.1'",
                    suggestion="Use 127.0.0.1 for reliability",
                )
            )

    return issues


def _check_database(project_name: str) -> list[ValidationIssue]:
    """Check database connectivity and migrations."""
    issues = []
    db = get_db()
    project = db.get_project(project_name)

    if not project:
        return issues

    # Check if project has a database configured in .env
    home = _get_project_home(project_name)
    env_file = home / ".env"
    has_database_url = False

    if env_file.exists():
        try:
            with open(env_file) as f:
                content = f.read()
                has_database_url = "DATABASE_URL" in content
        except PermissionError:
            pass

    if not has_database_url:
        # No database configured - this is fine, just informational
        return issues

    # Database is configured, try to verify it works
    try:
        import psycopg2

        db_name = f"hostkit_{project_name}"
        db_user = project_name

        # Try to connect via socket (peer auth) first, then TCP
        connected = False
        error_msg = ""

        # Try socket connection (peer auth)
        try:
            conn = psycopg2.connect(
                dbname=db_name,
                user=db_user,
                connect_timeout=5,
            )
            conn.close()
            connected = True
        except psycopg2.OperationalError:
            pass

        # Try TCP connection if socket failed
        if not connected:
            try:
                conn = psycopg2.connect(
                    dbname=db_name,
                    user=db_user,
                    host="127.0.0.1",
                    connect_timeout=5,
                )
                conn.close()
                connected = True
            except psycopg2.OperationalError as e:
                error_msg = str(e)

        if not connected:
            if "does not exist" in error_msg:
                issues.append(
                    ValidationIssue(
                        category="database",
                        severity="warning",
                        message="Database configured but does not exist yet",
                        suggestion="Run 'hostkit db create' to create the database",
                    )
                )
            else:
                # Just a warning - the app might handle its own connections
                issues.append(
                    ValidationIssue(
                        category="database",
                        severity="warning",
                        message="Could not verify database connection",
                        suggestion="Verify DATABASE_URL is correct and PostgreSQL is running",
                        details={"error": error_msg[:200]} if error_msg else None,
                    )
                )
    except ImportError:
        # psycopg2 not installed, skip database checks
        pass

    return issues


def _check_port(project_name: str, port: int) -> list[ValidationIssue]:
    """Check for port conflicts."""
    issues = []

    # First check if this project's service is running
    # If it is, skip the port check since the project itself is using the port
    try:
        result = subprocess.run(
            ["systemctl", "is-active", f"hostkit-{project_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip() == "active":
            # Service is running, port is expected to be in use
            return issues
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Check if port is in use by another process (project is not running)
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = {port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        output = result.stdout.strip()
        # If there's any output beyond the header, the port is in use
        lines = [line for line in output.split("\n") if line and not line.startswith("State")]
        if lines:
            issues.append(
                ValidationIssue(
                    category="port",
                    severity="error",
                    message=f"Port {port} is in use by another process",
                    suggestion="Check for conflicting services or change the project port",
                    details={"port": port, "process": lines[0][:200]},
                )
            )
    except (subprocess.SubprocessError, FileNotFoundError):
        # ss not available, skip check
        pass

    return issues


def _check_services(project_name: str, home: Path) -> tuple[list[str], list[ValidationIssue]]:
    """Check enabled services and return list of enabled services."""
    issues = []
    services = []

    # Check for service directories
    service_dirs = {
        "auth": ".auth",
        "payments": ".payments",
        "sms": ".sms",
        "booking": ".booking",
        "chatbot": ".chatbot",
    }

    for service, dirname in service_dirs.items():
        service_path = home / dirname
        if service_path.exists():
            services.append(service)

            # Check service health
            service_name = f"hostkit-{project_name}-{service}"
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", service_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status = result.stdout.strip()
                if status not in ("active", "activating"):
                    issues.append(
                        ValidationIssue(
                            category="services",
                            severity="warning",
                            message=f"{service.capitalize()} service is not running",
                            suggestion=(
                                f"Start with 'hostkit service start"
                                f" {project_name}"
                                f" --service {service}'"
                            ),
                            details={"service": service, "status": status},
                        )
                    )
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

    return services, issues


def validate_project(project_name: str) -> ValidationResult:
    """Run all validation checks for a project."""
    db = get_db()
    project = db.get_project(project_name)

    if not project:
        result = ValidationResult(project=project_name, valid=False)
        result.issues.append(
            ValidationIssue(
                category="project",
                severity="error",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        )
        return result

    runtime = project.get("runtime", "python")
    port = project.get("port", 8000)
    home = _get_project_home(project_name)

    result = ValidationResult(project=project_name, valid=True, runtime=runtime)

    # Run checks
    checks = [
        ("entrypoint", lambda: _check_entrypoint(project_name, runtime, home)),
        ("dependencies", lambda: _check_dependencies(project_name, runtime, home)),
        ("port", lambda: _check_port(project_name, port)),
        ("database", lambda: _check_database(project_name)),
    ]

    # Get services and add service-specific checks
    services, service_issues = _check_services(project_name, home)
    result.issues.extend(service_issues)

    # Add env check with service info
    checks.append(("env", lambda: _check_env_vars(project_name, runtime, home, services)))

    for check_name, check_fn in checks:
        try:
            issues = check_fn()
            result.issues.extend(issues)

            # Check if this check passed (no errors)
            has_errors = any(i.severity == "error" and i.category == check_name for i in issues)
            if not has_errors:
                result.checks_passed.append(check_name)
        except Exception as e:
            result.issues.append(
                ValidationIssue(
                    category=check_name,
                    severity="warning",
                    message=f"Check failed unexpectedly: {str(e)[:100]}",
                )
            )

    # Determine overall validity (no errors)
    result.valid = not any(i.severity == "error" for i in result.issues)

    return result


def _format_severity(severity: str) -> str:
    """Format severity with color."""
    colors = {
        "error": "red",
        "warning": "yellow",
        "info": "blue",
    }
    icons = {
        "error": "[✗]",
        "warning": "[!]",
        "info": "[i]",
    }
    return click.style(
        f"{icons.get(severity, '[?]')} {severity.upper()}", fg=colors.get(severity, "white")
    )


def _print_validation(result: ValidationResult) -> None:
    """Print validation result in human-readable format."""
    click.echo()

    # Header
    if result.valid:
        status = click.style("[✓] VALID", fg="green", bold=True)
    else:
        status = click.style("[✗] INVALID", fg="red", bold=True)

    click.echo(f"Validation: {result.project}  {status}")
    click.echo(f"Runtime: {result.runtime or 'unknown'}")
    click.echo()

    # Checks passed
    if result.checks_passed:
        click.echo(click.style("Passed:", fg="green"))
        for check in result.checks_passed:
            click.echo(f"  {click.style('✓', fg='green')} {check}")
        click.echo()

    # Issues
    if result.issues:
        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]
        infos = [i for i in result.issues if i.severity == "info"]

        if errors:
            click.echo(click.style("Errors:", fg="red", bold=True))
            for issue in errors:
                click.echo(f"  {_format_severity(issue.severity)}")
                click.echo(f"    {issue.message}")
                if issue.suggestion:
                    click.echo(f"    → {click.style(issue.suggestion, fg='cyan')}")
            click.echo()

        if warnings:
            click.echo(click.style("Warnings:", fg="yellow", bold=True))
            for issue in warnings:
                click.echo(f"  {_format_severity(issue.severity)}")
                click.echo(f"    {issue.message}")
                if issue.suggestion:
                    click.echo(f"    → {issue.suggestion}")
            click.echo()

        if infos:
            click.echo(click.style("Info:", fg="blue"))
            for issue in infos:
                click.echo(f"  {_format_severity(issue.severity)}")
                click.echo(f"    {issue.message}")
            click.echo()
    else:
        click.echo(click.style("All checks passed!", fg="green"))
        click.echo()

    # Summary
    error_count = sum(1 for i in result.issues if i.severity == "error")
    warning_count = sum(1 for i in result.issues if i.severity == "warning")

    summary_parts = []
    if error_count:
        summary_parts.append(click.style(f"{error_count} error(s)", fg="red"))
    if warning_count:
        summary_parts.append(click.style(f"{warning_count} warning(s)", fg="yellow"))

    if summary_parts:
        click.echo(f"Summary: {', '.join(summary_parts)}")

    if not result.valid:
        click.echo()
        click.echo("Fix the errors above before deploying.")


@click.command("validate")
@click.argument("project")
@click.option(
    "--fix",
    is_flag=True,
    help="Attempt to auto-fix common issues (coming soon)",
)
@click.pass_context
@project_access("project")
def validate(ctx: click.Context, project: str, fix: bool) -> None:
    """Validate a project before deployment.

    Runs pre-flight checks to catch common issues:

    \b
    - Entrypoint: App directory and main file exist
    - Dependencies: venv/node_modules installed
    - Environment: Required env vars set
    - Database: Connection works (if configured)
    - Port: No conflicts with other services
    - Services: Enabled services are running

    \b
    Examples:
        hostkit validate myapp           # Run all checks
        hostkit --json validate myapp    # JSON output for automation
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    result = validate_project(project)

    if formatter.json_mode:
        formatter.success(data=result.to_dict(), message="Validation complete")
    else:
        _print_validation(result)

    # Exit with non-zero if invalid
    if not result.valid:
        raise SystemExit(1)
