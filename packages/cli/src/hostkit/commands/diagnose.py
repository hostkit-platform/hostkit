"""Failure diagnosis CLI command for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.diagnosis_service import DiagnosisError, DiagnosisService


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


@click.command()
@click.argument("project")
@click.option("--verbose", "-v", is_flag=True, help="Include raw log excerpts in evidence")
@click.option("--check-db", is_flag=True, help="Also test database connectivity")
@click.option("--quick", "-q", is_flag=True, help="Quick status check only (no log analysis)")
@click.option("--run-test", is_flag=True, help="Run entrypoint directly and capture startup output")
@click.option(
    "--timeout",
    default=10,
    type=int,
    help="Timeout for --run-test in seconds (default: 10)",
)
@click.option("--no-restart", is_flag=True, help="Don't restart service after --run-test")
@click.pass_context
@project_owner("project")
def diagnose(
    ctx: click.Context,
    project: str,
    verbose: bool,
    check_db: bool,
    quick: bool,
    run_test: bool,
    timeout: int,
    no_restart: bool,
) -> None:
    """Diagnose project failures and suggest fixes.

    Analyzes deployment history, service logs, and system status to detect
    common failure patterns and provide AI-friendly suggestions.

    \b
    Patterns Detected:
      - Deploy-crash loops (rapid deploy-fail-restart cycles)
      - Missing Python modules (ImportError, ModuleNotFoundError)
      - Port conflicts (Address already in use)
      - Database connection failures
      - Memory exhaustion (OOM)
      - Permission errors
      - Syntax errors
      - File not found errors

    \b
    Startup Test (--run-test):
      Run the project's entrypoint command directly to capture startup errors
      that don't appear in systemd logs. Useful for debugging crashes where
      systemd only shows "exit code 1".

    \b
    Examples:
      hostkit diagnose myapp              # Full diagnosis
      hostkit diagnose myapp --quick      # Quick status only
      hostkit diagnose myapp --check-db   # Include database test
      hostkit diagnose myapp --verbose    # Include log excerpts
      hostkit diagnose myapp --run-test   # Run entrypoint and capture output
      hostkit diagnose myapp --run-test --timeout 30  # Longer timeout
      hostkit diagnose myapp --run-test --no-restart  # Don't restart after
      hostkit diagnose myapp --json       # AI-friendly output
    """
    formatter = get_formatter(ctx)

    try:
        service = DiagnosisService()

        if run_test:
            # Run startup test to capture entrypoint output
            result = service.run_startup_test(
                project,
                timeout_seconds=timeout,
                restart_after=not no_restart,
            )

            if formatter.json_mode:
                formatter.success(data=result, message="Startup test complete")
            else:
                _print_startup_test(result)
        elif quick:
            # Quick status check
            status = service.get_quick_status(project)

            if formatter.json_mode:
                formatter.success(data=status, message="Quick status retrieved")
            else:
                _print_quick_status(status)
        else:
            # Full diagnosis
            result = service.diagnose(
                project,
                verbose=verbose,
                check_db=check_db,
            )

            if formatter.json_mode:
                formatter.success(data=result.to_dict(), message="Diagnosis complete")
            else:
                _print_diagnosis(result)

    except DiagnosisError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


def _print_quick_status(status: dict) -> None:
    """Print quick status in human-readable format."""
    project = status["project"]
    health = status["health"]
    running = status["service_running"]
    service_status = status["service_status"]
    consecutive_failures = status["consecutive_failures"]
    failures_1h = status["failures_1h"]

    click.echo()
    click.echo(click.style(f"Quick Status: {project}", bold=True))
    click.echo()

    # Health indicator
    health_color = {
        "healthy": "green",
        "degraded": "yellow",
        "critical": "red",
    }.get(health, "white")
    health_icon = {
        "healthy": "[OK]",
        "degraded": "[!]",
        "critical": "[X]",
    }.get(health, "[?]")
    styled_health = click.style(
        health_icon + " " + health.upper(),
        fg=health_color,
        bold=True,
    )
    click.echo(f"  Health:    {styled_health}")

    # Service status
    if running:
        click.echo(f"  Service:   {click.style('Running', fg='green')}")
    else:
        click.echo(f"  Service:   {click.style(service_status.capitalize(), fg='red')}")

    # Failure stats
    if consecutive_failures > 0:
        fail_color = "red" if consecutive_failures >= 3 else "yellow"
        styled_fails = click.style(
            str(consecutive_failures),
            fg=fail_color,
        )
        click.echo(f"  Consecutive failures: {styled_fails}")

    if failures_1h > 0:
        click.echo(f"  Failures (1h):        {click.style(str(failures_1h), fg='yellow')}")

    click.echo()

    if health == "critical":
        click.echo(
            click.style(
                "Run 'hostkit diagnose {project}' for detailed analysis",
                fg="yellow",
            )
        )
    elif health == "degraded":
        click.echo("Run full diagnosis for pattern detection: hostkit diagnose " + project)

    click.echo()


def _print_diagnosis(result) -> None:
    """Print full diagnosis in human-readable format."""
    click.echo()
    click.echo(click.style(f"Diagnosis Report: {result.project}", bold=True, underline=True))
    click.echo(f"Diagnosed at: {result.diagnosed_at}")
    click.echo()

    # Overall health
    health = result.overall_health
    health_color = {
        "healthy": "green",
        "degraded": "yellow",
        "critical": "red",
    }.get(health, "white")
    health_icon = {
        "healthy": "[OK]",
        "degraded": "[!]",
        "critical": "[X]",
    }.get(health, "[?]")
    styled_health = click.style(
        health_icon + " " + health.upper(),
        fg=health_color,
        bold=True,
    )
    click.echo(f"Overall Health: {styled_health}")
    click.echo()

    # Service status
    svc = result.service_status
    click.echo(click.style("Service Status:", bold=True))
    if svc.get("running"):
        click.echo(f"  Status:    {click.style('Running', fg='green')}")
    else:
        status_text = svc.get("status", "unknown").capitalize()
        click.echo(f"  Status:    {click.style(status_text, fg='red')}")

    if svc.get("recent_restarts", 0) > 0:
        click.echo(f"  Restarts (1h): {click.style(str(svc['recent_restarts']), fg='yellow')}")

    if svc.get("exit_code") is not None:
        click.echo(f"  Exit code: {svc['exit_code']}")

    if svc.get("last_failure"):
        click.echo(f"  Last failure: {svc['last_failure']}")
    click.echo()

    # Database status (if checked)
    if result.database_status:
        db = result.database_status
        click.echo(click.style("Database Status:", bold=True))
        if db.get("connected"):
            latency = db.get("latency_ms", "?")
            click.echo(f"  Connected: {click.style('Yes', fg='green')} ({latency}ms)")
        elif db.get("error"):
            click.echo(f"  Connected: {click.style('No', fg='red')}")
            click.echo(f"  Error:     {db['error'][:100]}")
        click.echo()

    # Recent failures summary
    failures = result.recent_failures
    click.echo(click.style("Recent Failures (1h):", bold=True))
    click.echo(f"  Deploy attempts:      {failures.get('deploys_1h', 0)}")
    click.echo(f"  Deploy failures:      {failures.get('failures_1h', 0)}")
    click.echo(f"  Service crashes:      {failures.get('service_crashes_1h', 0)}")
    click.echo(f"  Consecutive failures: {failures.get('consecutive_failures', 0)}")
    click.echo()

    # Patterns detected
    if result.patterns:
        click.echo(click.style("Detected Patterns:", bold=True))
        click.echo()

        for i, pattern in enumerate(result.patterns, 1):
            severity_color = {
                "critical": "red",
                "high": "yellow",
                "medium": "cyan",
                "low": "white",
            }.get(pattern.severity, "white")

            severity_icon = {
                "critical": "[!!!]",
                "high": "[!!]",
                "medium": "[!]",
                "low": "[i]",
            }.get(pattern.severity, "[?]")

            styled_icon = click.style(severity_icon, fg=severity_color)
            styled_type = click.style(pattern.pattern_type, bold=True)
            click.echo(f"  {i}. {styled_icon} {styled_type}")
            styled_sev = click.style(
                pattern.severity.upper(),
                fg=severity_color,
            )
            click.echo(f"     Severity:    {styled_sev}")
            click.echo(f"     Occurrences: {pattern.occurrences} (window: {pattern.window})")

            if pattern.common_error:
                # Truncate long errors
                error_text = pattern.common_error[:100]
                if len(pattern.common_error) > 100:
                    error_text += "..."
                click.echo(f"     Error:       {error_text}")

            if pattern.suggestion:
                click.echo(f"     {click.style('Suggestion:', fg='green')} {pattern.suggestion}")

            if pattern.evidence:
                click.echo("     Evidence:")
                for ev in pattern.evidence[:2]:  # Show max 2 evidence items
                    ev_text = ev[:80] + "..." if len(ev) > 80 else ev
                    click.echo(f"       - {ev_text}")

            if pattern.details:
                for key, value in list(pattern.details.items())[:3]:  # Show max 3 details
                    click.echo(f"     {key}: {value}")

            click.echo()
    else:
        click.echo(click.style("No failure patterns detected.", fg="green"))
        click.echo()

    # Recommendations
    if result.recommendations:
        click.echo(click.style("Recommendations:", bold=True))
        for i, rec in enumerate(result.recommendations, 1):
            click.echo(f"  {i}. {rec}")
        click.echo()

    # Footer with next steps
    if result.overall_health == "critical":
        click.echo(click.style("Action Required:", fg="red", bold=True))
        click.echo("  Address critical patterns before deploying again.")
        click.echo("  View logs: hostkit service logs " + result.project)
        click.echo()
    elif result.overall_health == "degraded":
        click.echo(click.style("Warning:", fg="yellow", bold=True))
        click.echo("  Project is operational but experiencing issues.")
        click.echo("  Consider addressing detected patterns to improve stability.")
        click.echo()


def _print_startup_test(result: dict) -> None:
    """Print startup test results in human-readable format."""
    project = result["project"]
    runtime = result["runtime"]
    command = result["command"]
    exit_code = result["exit_code"]
    timed_out = result["timed_out"]
    stdout = result["stdout"]
    stderr = result["stderr"]

    click.echo()
    click.echo(click.style(f"Startup Test: {project}", bold=True, underline=True))
    click.echo()

    click.echo(f"  Runtime:  {runtime}")
    click.echo(f"  Command:  {command}")

    if result["service_was_running"]:
        click.echo(f"  Service:  {click.style('Stopped temporarily', fg='yellow')}")
    else:
        click.echo(f"  Service:  {click.style('Was not running', fg='cyan')}")

    click.echo()

    # Exit status
    if timed_out:
        click.echo(f"  Result:   {click.style('Timed out (process kept running)', fg='yellow')}")
        click.echo("            This usually means the app started successfully.")
    elif exit_code == 0:
        click.echo(f"  Result:   {click.style('Exit code 0 (success)', fg='green')}")
    else:
        click.echo(f"  Result:   {click.style(f'Exit code {exit_code} (crashed)', fg='red')}")

    click.echo()

    # Output
    if stderr:
        click.echo(click.style("STDERR (error output):", bold=True, fg="red"))
        click.echo("-" * 60)
        for line in stderr.strip().split("\n")[-50:]:  # Last 50 lines
            click.echo(f"  {line}")
        click.echo("-" * 60)
        click.echo()

    if stdout:
        click.echo(click.style("STDOUT (standard output):", bold=True))
        click.echo("-" * 60)
        for line in stdout.strip().split("\n")[-50:]:  # Last 50 lines
            click.echo(f"  {line}")
        click.echo("-" * 60)
        click.echo()

    if not stdout and not stderr:
        click.echo(click.style("No output captured.", fg="yellow"))
        click.echo()

    # Service restart status
    if result["service_restarted"]:
        click.echo(f"  Service:  {click.style('Restarted', fg='green')}")
    elif result["service_was_running"]:
        msg = "Not restarted (use hostkit service start)"
        click.echo(f"  Service:  {click.style(msg, fg='yellow')}")

    click.echo()
