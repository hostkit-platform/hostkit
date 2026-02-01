"""Main CLI entry point for HostKit."""

import click

from hostkit import __version__
from hostkit.access import get_access_context
from hostkit.output import OutputFormatter


@click.group()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.version_option(version=__version__, prog_name="hostkit")
@click.pass_context
def cli(ctx: click.Context, output_json: bool) -> None:
    """HostKit - AI-agent-native VPS management CLI.

    Manage projects, databases, services, and more from the command line.
    Use --json flag for machine-readable output.

    Commands can be run as root (full access) or as a project user
    (limited to own project resources).
    """
    ctx.ensure_object(dict)
    ctx.obj["formatter"] = OutputFormatter(json_mode=output_json)
    ctx.obj["json_mode"] = output_json
    ctx.obj["access"] = get_access_context()


# Import and register commands
from hostkit.commands import status  # noqa: E402
from hostkit.commands import project  # noqa: E402
from hostkit.commands import db  # noqa: E402
from hostkit.commands import redis  # noqa: E402
from hostkit.commands import service  # noqa: E402
from hostkit.commands import nginx  # noqa: E402
from hostkit.commands import ssl  # noqa: E402
from hostkit.commands import dns  # noqa: E402
from hostkit.commands import mail  # noqa: E402
from hostkit.commands import storage  # noqa: E402
from hostkit.commands import log  # noqa: E402
from hostkit.commands import backup  # noqa: E402
from hostkit.commands import auth  # noqa: E402
from hostkit.commands import ssh  # noqa: E402
from hostkit.commands import env  # noqa: E402
from hostkit.commands import operator  # noqa: E402
from hostkit.commands import secrets  # noqa: E402
from hostkit.commands import cron  # noqa: E402
from hostkit.commands import worker  # noqa: E402
from hostkit.commands import vector  # noqa: E402
from hostkit.commands import claude  # noqa: E402
from hostkit.commands import checkpoint  # noqa: E402
from hostkit.commands import alert  # noqa: E402
from hostkit.commands.deploy import deploy  # noqa: E402
from hostkit.commands.migrate import migrate  # noqa: E402
from hostkit.commands.health import health  # noqa: E402
from hostkit.commands.rollback import rollback  # noqa: E402
from hostkit.commands.provision import provision  # noqa: E402
from hostkit.commands import ratelimit  # noqa: E402
from hostkit.commands.deploys import deploys  # noqa: E402
from hostkit.commands.diagnose import diagnose  # noqa: E402
from hostkit.commands import autopause  # noqa: E402
from hostkit.commands.resume import resume  # noqa: E402
from hostkit.commands.sandbox import sandbox  # noqa: E402
from hostkit.commands import limits  # noqa: E402
from hostkit.commands.git import git  # noqa: E402
from hostkit.commands.environment import environment  # noqa: E402
from hostkit.commands.events import events  # noqa: E402
from hostkit.commands.metrics import metrics  # noqa: E402
from hostkit.commands.capabilities import capabilities  # noqa: E402
from hostkit.commands import image  # noqa: E402
from hostkit.commands import payments  # noqa: E402
from hostkit.commands import sms  # noqa: E402
from hostkit.commands import voice  # noqa: E402
from hostkit.commands import booking
from hostkit.commands import r2  # noqa: E402
from hostkit.commands import chatbot  # noqa: E402
from hostkit.commands import docs  # noqa: E402
from hostkit.commands import query  # noqa: E402
from hostkit.commands import permissions  # noqa: E402
from hostkit.commands.validate import validate
from hostkit.commands.exec import exec_cmd  # noqa: E402

cli.add_command(status.status)
cli.add_command(project.project)
cli.add_command(db.db)
cli.add_command(redis.redis)
cli.add_command(service.service)
cli.add_command(nginx.nginx)
cli.add_command(ssl.ssl)
cli.add_command(dns.dns)
cli.add_command(mail.mail)
cli.add_command(storage.storage)
cli.add_command(storage.minio)  # Alias for storage
cli.add_command(log.log)
cli.add_command(backup.backup)
cli.add_command(auth.auth)
cli.add_command(payments.payments)
cli.add_command(sms.sms)
cli.add_command(voice.voice)
cli.add_command(booking.booking)
cli.add_command(r2.r2)
cli.add_command(chatbot.chatbot)
cli.add_command(docs.docs)
cli.add_command(query.query)
cli.add_command(ssh.ssh)
cli.add_command(env.env)
cli.add_command(operator.operator)
cli.add_command(secrets.secrets)
cli.add_command(cron.cron)
cli.add_command(worker.worker)
cli.add_command(vector.vector)
cli.add_command(claude.claude)
cli.add_command(checkpoint.checkpoint)
cli.add_command(alert.alert)
cli.add_command(deploy)
cli.add_command(migrate)
cli.add_command(health)
cli.add_command(rollback)
cli.add_command(provision)
cli.add_command(ratelimit.ratelimit)
cli.add_command(deploys)
cli.add_command(diagnose)
cli.add_command(autopause.autopause)
cli.add_command(resume)
cli.add_command(sandbox)
cli.add_command(limits.limits)
cli.add_command(git)
cli.add_command(environment)
cli.add_command(events)
cli.add_command(metrics)
cli.add_command(capabilities)
cli.add_command(image.image)
cli.add_command(permissions.permissions)
cli.add_command(validate)
cli.add_command(exec_cmd)
