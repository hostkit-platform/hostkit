"""Payment service management commands for HostKit."""

import click

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.payment_service import PaymentService, PaymentServiceError


@click.group()
@click.pass_context
def payments(ctx: click.Context) -> None:
    """Manage per-project payment services.

    Enable Stripe Connect Express accounts for payment processing,
    subscriptions, and customer management.
    """
    pass


@payments.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def payments_enable(ctx: click.Context, project: str) -> None:
    """Enable payment service for a project.

    Creates a Stripe Express account and returns onboarding URL.
    Once the project owner completes onboarding, payments will be active.

    Example:
        hostkit payments enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = PaymentService()

    try:
        result = service.enable_payments(project=project)

        formatter.success(
            message=f"Payment service enabled for '{project}'",
            data={
                "project": project,
                "stripe_account_id": result["stripe_account_id"],
                "onboarding_url": result["onboarding_url"],
                "payment_port": result["payment_port"],
                "payment_db": result["payment_db"],
                "status": "pending_onboarding",
                "next_steps": "Complete Stripe onboarding at the URL above",
            },
        )

    except PaymentServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@payments.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def payments_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable payment service for a project.

    Removes the payment database and configuration.
    Requires --force to confirm.

    WARNING: This will delete all payment data!

    Example:
        hostkit payments disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = PaymentService()

    try:
        service.disable_payments(project=project, force=force)

        formatter.success(
            message=f"Payment service disabled for '{project}'",
            data={
                "project": project,
                "payment_db_deleted": True,
                "stripe_account_note": "Stripe account must be closed manually in Stripe dashboard",
            },
        )

    except PaymentServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@payments.command("status")
@click.argument("project")
@click.pass_context
@project_access("project")
def payments_status(ctx: click.Context, project: str) -> None:
    """Show payment service status for a project.

    Displays Stripe account status, capabilities, and configuration.

    Example:
        hostkit payments status myapp
        hostkit --json payments status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = PaymentService()

    try:
        status = service.get_payment_status(project=project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Payment status for '{project}'",
                data=status,
            )
        else:
            click.echo(f"\nPayment Status: {project}")
            click.echo("-" * 60)

            if not status["enabled"]:
                click.echo("  Status: DISABLED")
                click.echo(f"\n  Enable with: hostkit payments enable {project}")
            else:
                click.echo(f"  Status: {status['account_status'].upper()}")
                click.echo(f"  Payment Port: {status['payment_port']}")
                click.echo(f"  Payment DB: {status['payment_db']}")
                click.echo(f"  Stripe Account: {status['stripe_account_id']}")
                click.echo(f"  Currency: {status['currency']}")
                click.echo(f"  Charges Enabled: {status['charges_enabled']}")
                click.echo(f"  Payouts Enabled: {status['payouts_enabled']}")

                if status["account_status"] == "pending":
                    click.echo(f"\n  Complete onboarding: {status.get('onboarding_url', 'N/A')}")

    except PaymentServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@payments.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def payments_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View payment service logs for a project.

    Shows logs from the project's payment service.
    Use --follow to stream logs in real-time.

    Example:
        hostkit payments logs myapp
        hostkit payments logs myapp --lines 50
        hostkit payments logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = PaymentService()

    try:
        # Verify payments is enabled for project
        if not service.payment_is_enabled(project):
            raise PaymentServiceError(
                code="PAYMENT_NOT_ENABLED",
                message=f"Payment service is not enabled for '{project}'",
                suggestion=f"Enable payments first with 'hostkit payments enable {project}'",
            )

        service_name = f"hostkit-{project}-payment"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following payment logs for {project}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            proc = subprocess.Popen(
                ["journalctl", "-u", f"{service_name}.service", "-f", "-n", str(lines)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            try:
                for line in iter(proc.stdout.readline, b""):
                    sys.stdout.write(line.decode())
                    sys.stdout.flush()
            except KeyboardInterrupt:
                proc.terminate()
                click.echo("\n--- Log stream ended ---")
        else:
            result = subprocess.run(
                ["journalctl", "-u", f"{service_name}.service", "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
            )

            logs = result.stdout

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Payment logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nPayment logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except PaymentServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)
