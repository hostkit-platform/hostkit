"""Authentication service management commands for HostKit."""

import subprocess

import click

from hostkit.access import project_access, root_only
from hostkit.output import OutputFormatter
from hostkit.services.auth_service import AuthService, AuthServiceError
from hostkit.services.secrets_service import SecretsServiceError, get_secrets_service


@click.group()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage per-project authentication services.

    Enable Supabase-like authentication for your projects with OAuth,
    email/password, magic links, and anonymous sessions.
    """
    pass


@auth.command("enable")
@click.argument("project")
@click.option("--google-client-id", help="Google OAuth client ID (for native iOS/Android apps)")
@click.option("--google-web-client-id", help="Google OAuth web client ID (for web OAuth)")
@click.option("--google-client-secret", help="Google OAuth client secret")
@click.option("--apple-client-id", help="Apple Sign-In client ID")
@click.option("--apple-team-id", help="Apple Developer Team ID")
@click.option("--apple-key-id", help="Apple Sign-In key ID")
@click.option("--no-email", is_flag=True, help="Disable email/password auth")
@click.option("--no-magic-link", is_flag=True, help="Disable magic link auth")
@click.option("--no-anonymous", is_flag=True, help="Disable anonymous sessions")
@click.pass_context
@root_only
def auth_enable(
    ctx: click.Context,
    project: str,
    google_client_id: str | None,
    google_web_client_id: str | None,
    google_client_secret: str | None,
    apple_client_id: str | None,
    apple_team_id: str | None,
    apple_key_id: str | None,
    no_email: bool,
    no_magic_link: bool,
    no_anonymous: bool,
) -> None:
    """Enable authentication service for a project.

    Creates a dedicated auth database, generates JWT signing keys, and
    configures the project for authentication.

    OAuth providers can be configured at enable time or later using
    'hostkit auth config'.

    Example:
        hostkit auth enable myapp
        hostkit auth enable myapp --google-client-id=xxx --google-client-secret=yyy
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        config = service.enable_auth(
            project=project,
            google_client_id=google_client_id,
            google_web_client_id=google_web_client_id,
            google_client_secret=google_client_secret,
            apple_client_id=apple_client_id,
            apple_team_id=apple_team_id,
            apple_key_id=apple_key_id,
            email_enabled=not no_email,
            magic_link_enabled=not no_magic_link,
            anonymous_enabled=not no_anonymous,
        )

        # Auto-sync JWT public key to project's .env file
        from pathlib import Path

        env_path = Path(f"/home/{project}/.env")
        public_key_path = Path(f"/home/{project}/.auth/jwt_public.pem")

        key_synced = False
        if env_path.exists() and public_key_path.exists():
            pem_content = public_key_path.read_text().strip()
            escaped_key = pem_content.replace("\n", "\\n")

            with open(env_path) as f:
                lines = f.readlines()

            updated = False
            new_lines = []
            for line in lines:
                if line.startswith("AUTH_JWT_PUBLIC_KEY="):
                    new_lines.append(f'AUTH_JWT_PUBLIC_KEY="{escaped_key}"\n')
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                comment = "# JWT Public Key (auto-synced by HostKit Auth)"
                new_lines.append(f'\n{comment}\nAUTH_JWT_PUBLIC_KEY="{escaped_key}"\n')

            with open(env_path, "w") as f:
                f.writelines(new_lines)
            key_synced = True

        providers = []
        if config.email_enabled:
            providers.append("email")
        if config.magic_link_enabled:
            providers.append("magic_link")
        if config.anonymous_enabled:
            providers.append("anonymous")
        if config.google_client_id:
            providers.append("google")
        if config.apple_client_id:
            providers.append("apple")

        formatter.success(
            message=f"Authentication enabled for '{project}'",
            data={
                "project": project,
                "auth_port": config.port,
                "auth_db": config.auth_db,
                "auth_db_user": config.auth_db_user,
                "jwt_public_key": config.jwt_public_key_path,
                "jwt_key_synced": key_synced,
                "providers": providers,
            },
        )

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@root_only
def auth_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable authentication service for a project.

    Removes the auth database, JWT keys, and configuration.
    Requires --force to confirm.

    WARNING: This will delete all user accounts and sessions!

    Example:
        hostkit auth disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        service.disable_auth(project=project, force=force)

        formatter.success(
            message=f"Authentication disabled for '{project}'",
            data={
                "project": project,
                "auth_db_deleted": f"{project}_auth_db",
                "auth_role_deleted": f"{project}_auth_user",
                "jwt_keys_deleted": True,
            },
        )

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("config")
@click.argument("project")
@click.option("--set", "settings", multiple=True, help="Set config value (key=value)")
@click.option("--base-url", help="Base URL for OAuth callbacks (e.g., https://myapp.com)")
@click.option("--google-client-id", help="Google OAuth client ID (for native iOS/Android apps)")
@click.option("--google-web-client-id", help="Google OAuth web client ID (for web OAuth)")
@click.option("--google-client-secret", help="Google OAuth client secret")
@click.option("--apple-client-id", help="Apple Sign-In client ID")
@click.option("--apple-team-id", help="Apple Developer Team ID")
@click.option("--apple-key-id", help="Apple Sign-In key ID")
@click.option("--email/--no-email", default=None, help="Enable/disable email/password auth")
@click.option("--magic-link/--no-magic-link", default=None, help="Enable/disable magic links")
@click.option("--anonymous/--no-anonymous", default=None, help="Enable/disable anonymous sessions")
@click.option("--from-secrets", is_flag=True, help="Read OAuth credentials from secrets vault")
@click.option(
    "--from-platform",
    is_flag=True,
    help="Read OAuth credentials from platform config (/etc/hostkit/oauth.ini)",
)
@click.option("--no-restart", is_flag=True, help="Don't restart auth service after config changes")
@click.pass_context
@project_access("project")
def auth_config(
    ctx: click.Context,
    project: str,
    settings: tuple[str, ...],
    base_url: str | None,
    google_client_id: str | None,
    google_web_client_id: str | None,
    google_client_secret: str | None,
    apple_client_id: str | None,
    apple_team_id: str | None,
    apple_key_id: str | None,
    email: bool | None,
    magic_link: bool | None,
    anonymous: bool | None,
    from_secrets: bool,
    from_platform: bool,
    no_restart: bool,
) -> None:
    """View or update authentication configuration.

    Without options, shows current configuration.
    With options, updates the specified settings.

    Use --from-platform to inject platform-level OAuth credentials from
    /etc/hostkit/oauth.ini. This is the recommended way to enable Google/Apple
    OAuth for projects, as it uses the centrally managed platform credentials.

    Use --from-secrets to read OAuth credentials from the project's secrets vault.
    This keeps credentials out of shell history and process lists.

    Expected secret keys (for --from-secrets):
        GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
        APPLE_CLIENT_ID, APPLE_TEAM_ID, APPLE_KEY_ID

    Example:
        hostkit auth config myapp
        hostkit auth config myapp --from-platform
        hostkit auth config myapp --google-client-id=xxx --google-client-secret=yyy
        hostkit auth config myapp --from-secrets
        hostkit auth config myapp --no-anonymous
        hostkit auth config myapp --set access_token_expire_minutes=30
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        # Initialize apple_private_key (may be set by --from-secrets or --from-platform)
        apple_private_key = None

        # Read OAuth credentials from secrets vault if --from-secrets is specified
        if from_secrets:
            secrets_service = get_secrets_service()

            # Read Google OAuth credentials if not already specified via CLI
            if google_client_id is None:
                google_client_id = secrets_service.get_secret(project, "GOOGLE_CLIENT_ID")
            if google_web_client_id is None:
                google_web_client_id = secrets_service.get_secret(project, "GOOGLE_WEB_CLIENT_ID")
            if google_client_secret is None:
                google_client_secret = secrets_service.get_secret(project, "GOOGLE_CLIENT_SECRET")

            # Read Apple Sign-In credentials if not already specified via CLI
            if apple_client_id is None:
                apple_client_id = secrets_service.get_secret(project, "APPLE_CLIENT_ID")
            if apple_team_id is None:
                apple_team_id = secrets_service.get_secret(project, "APPLE_TEAM_ID")
            if apple_key_id is None:
                apple_key_id = secrets_service.get_secret(project, "APPLE_KEY_ID")

            # Read Apple private key (for token signing)
            apple_private_key = secrets_service.get_secret(project, "APPLE_PRIVATE_KEY")

            # Check if we found any OAuth credentials
            found_secrets = []
            if google_client_id:
                found_secrets.append("GOOGLE_CLIENT_ID")
            if google_web_client_id:
                found_secrets.append("GOOGLE_WEB_CLIENT_ID")
            if google_client_secret:
                found_secrets.append("GOOGLE_CLIENT_SECRET")
            if apple_client_id:
                found_secrets.append("APPLE_CLIENT_ID")
            if apple_team_id:
                found_secrets.append("APPLE_TEAM_ID")
            if apple_key_id:
                found_secrets.append("APPLE_KEY_ID")
            if apple_private_key:
                found_secrets.append("APPLE_PRIVATE_KEY")

            if not found_secrets:
                formatter.error(
                    code="NO_SECRETS_FOUND",
                    message=f"No OAuth secrets found in vault for '{project}'",
                    suggestion=(
                        f"Store secrets with 'hostkit secrets set"
                        f" {project} GOOGLE_CLIENT_ID' or define"
                        f" them via 'hostkit secrets define"
                        f" {project} --from .env.example'"
                    ),
                )
                raise SystemExit(1)

        # Read OAuth credentials from platform config if --from-platform is specified
        if from_platform:
            import configparser
            from pathlib import Path

            platform_config_path = Path("/etc/hostkit/oauth.ini")

            if not platform_config_path.exists():
                formatter.error(
                    code="PLATFORM_CONFIG_NOT_FOUND",
                    message="Platform OAuth config not found at /etc/hostkit/oauth.ini",
                    suggestion=(
                        "Platform OAuth credentials must be configured by HostKit administrator"
                    ),
                )
                raise SystemExit(1)

            config = configparser.ConfigParser()
            config.read(platform_config_path)

            # Read Google OAuth credentials if not already specified via CLI
            if config.has_section("google"):
                if google_client_id is None:
                    google_client_id = config.get("google", "client_id", fallback=None)
                if google_web_client_id is None:
                    google_web_client_id = config.get("google", "web_client_id", fallback=None)
                if google_client_secret is None:
                    google_client_secret = config.get("google", "client_secret", fallback=None)

            # Read Apple Sign-In credentials if not already specified via CLI
            if config.has_section("apple"):
                if apple_client_id is None:
                    apple_client_id = config.get("apple", "client_id", fallback=None)
                if apple_team_id is None:
                    apple_team_id = config.get("apple", "team_id", fallback=None)
                if apple_key_id is None:
                    apple_key_id = config.get("apple", "key_id", fallback=None)

                # Read Apple private key from file
                apple_key_path = config.get("apple", "private_key_path", fallback=None)
                if apple_key_path:
                    key_path = Path(apple_key_path)
                    if key_path.exists():
                        apple_private_key = key_path.read_text()

            # Check if we found any OAuth credentials
            found_platform = []
            if google_client_id:
                found_platform.append("GOOGLE_CLIENT_ID")
            if google_web_client_id:
                found_platform.append("GOOGLE_WEB_CLIENT_ID")
            if google_client_secret:
                found_platform.append("GOOGLE_CLIENT_SECRET")
            if apple_client_id:
                found_platform.append("APPLE_CLIENT_ID")
            if apple_team_id:
                found_platform.append("APPLE_TEAM_ID")
            if apple_key_id:
                found_platform.append("APPLE_KEY_ID")
            if apple_private_key:
                found_platform.append("APPLE_PRIVATE_KEY")

            if not found_platform:
                formatter.error(
                    code="NO_PLATFORM_CREDENTIALS",
                    message="No OAuth credentials found in platform config",
                    suggestion=(
                        "Platform OAuth credentials must be"
                        " configured by HostKit administrator"
                        " in /etc/hostkit/oauth.ini"
                    ),
                )
                raise SystemExit(1)

        # Check if any updates were requested
        has_updates = any(
            [
                settings,
                base_url is not None,
                google_client_id is not None,
                google_web_client_id is not None,
                google_client_secret is not None,
                apple_client_id is not None,
                apple_team_id is not None,
                apple_key_id is not None,
                email is not None,
                magic_link is not None,
                anonymous is not None,
            ]
        )

        if has_updates:
            # Handle base_url update (modifies config.py directly)
            base_url_updated = False
            if base_url is not None:
                import re
                from pathlib import Path

                config_path = Path(f"/home/{project}/.auth/config.py")
                if config_path.exists():
                    content = config_path.read_text()
                    # Update the base_url line in config.py
                    new_content = re.sub(
                        r'base_url: str = "[^"]*"', f'base_url: str = "{base_url}"', content
                    )
                    if new_content != content:
                        config_path.write_text(new_content)
                        base_url_updated = True
                else:
                    formatter.error(
                        code="AUTH_NOT_ENABLED",
                        message=f"Auth config not found for '{project}'",
                        suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
                    )
                    raise SystemExit(1)

            # Inject OAuth credentials into .env if using --from-secrets or --from-platform
            # The auth service reads these from the project's .env file
            oauth_env_injected = False
            if from_secrets or from_platform:
                from hostkit.services.env_service import EnvService

                env_service = EnvService()

                # Inject Google credentials
                if google_client_id:
                    env_service.set_env(project, "GOOGLE_CLIENT_ID", google_client_id)
                    oauth_env_injected = True
                if google_web_client_id:
                    env_service.set_env(project, "GOOGLE_WEB_CLIENT_ID", google_web_client_id)
                    oauth_env_injected = True
                if google_client_secret:
                    env_service.set_env(project, "GOOGLE_CLIENT_SECRET", google_client_secret)
                    oauth_env_injected = True

                # Inject Apple credentials
                if apple_client_id:
                    env_service.set_env(project, "APPLE_CLIENT_ID", apple_client_id)
                    oauth_env_injected = True
                if apple_team_id:
                    env_service.set_env(project, "APPLE_TEAM_ID", apple_team_id)
                    oauth_env_injected = True
                if apple_key_id:
                    env_service.set_env(project, "APPLE_KEY_ID", apple_key_id)
                    oauth_env_injected = True
                if apple_private_key:
                    env_service.set_env(project, "APPLE_PRIVATE_KEY", apple_private_key)
                    oauth_env_injected = True

            # Update configuration
            updated = service.update_auth_config(
                project=project,
                google_client_id=google_client_id,
                google_web_client_id=google_web_client_id,
                google_client_secret=google_client_secret,
                apple_client_id=apple_client_id,
                apple_team_id=apple_team_id,
                apple_key_id=apple_key_id,
                email_enabled=email,
                magic_link_enabled=magic_link,
                anonymous_enabled=anonymous,
            )

            # Restart auth service to pick up new config (unless --no-restart)
            service_restarted = False
            if not no_restart:
                auth_service_name = f"hostkit-{project}-auth"
                try:
                    subprocess.run(
                        ["systemctl", "restart", auth_service_name],
                        check=True,
                        capture_output=True,
                    )
                    service_restarted = True
                except subprocess.CalledProcessError:
                    pass  # Service might not be running, that's ok

            updated["service_restarted"] = service_restarted
            if base_url is not None:
                updated["base_url"] = base_url
                updated["base_url_updated"] = base_url_updated
            if (from_secrets or from_platform) and oauth_env_injected:
                updated["oauth_env_injected"] = oauth_env_injected

            formatter.success(
                message=f"Configuration updated for '{project}'"
                + (" (auth service restarted)" if service_restarted else ""),
                data=updated,
            )
        else:
            # Show current configuration
            config = service.get_auth_config_details(project)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Auth configuration for '{project}'",
                    data=config,
                )
            else:
                click.echo(f"\nAuth Configuration: {project}")
                click.echo("-" * 50)
                click.echo(f"  Auth Port:     {config['auth_port']}")
                click.echo(f"  Auth Database: {config['auth_db']}")
                click.echo(f"  Auth DB User:  {config['auth_db_user']}")

                click.echo("\n  Providers:")
                click.echo(
                    f"    Email/Password: {'Enabled' if config['email_enabled'] else 'Disabled'}"
                )
                ml_status = "Enabled" if config["magic_link_enabled"] else "Disabled"
                click.echo(f"    Magic Links:    {ml_status}")
                anon_status = "Enabled" if config["anonymous_enabled"] else "Disabled"
                click.echo(f"    Anonymous:      {anon_status}")

                click.echo("\n  OAuth:")
                if config.get("google_client_id"):
                    click.echo(f"    Google: Configured (ID: {config['google_client_id'][:12]}...)")
                else:
                    click.echo("    Google: Not configured")

                if config.get("apple_client_id"):
                    click.echo(f"    Apple:  Configured (ID: {config['apple_client_id'][:12]}...)")
                else:
                    click.echo("    Apple:  Not configured")

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)
    except SecretsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("status")
@click.argument("project", required=False)
@click.pass_context
def auth_status(ctx: click.Context, project: str | None) -> None:
    """Show authentication status for projects.

    Without arguments, shows auth status for all projects (root only).
    With a project name, shows detailed status for that project.

    Example:
        hostkit auth status
        hostkit auth status myapp
        hostkit --json auth status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    # Access control: if project specified, check access; otherwise require root
    from hostkit.access import AccessDeniedError, require_project_access, require_root

    try:
        if project:
            require_project_access(project)
        else:
            require_root()
    except AccessDeniedError as e:
        formatter.error(code="ACCESS_DENIED", message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

    try:
        status = service.get_auth_status(project=project)

        if project:
            # Single project detailed status
            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Auth status for '{project}'",
                    data=status,
                )
            else:
                click.echo(f"\nAuth Status: {project}")
                click.echo("-" * 50)

                if not status["enabled"]:
                    click.echo("  Status: DISABLED")
                    click.echo("\n  Enable with: hostkit auth enable " + project)
                else:
                    click.echo("  Status: ENABLED")
                    click.echo(f"  Auth Port: {status['auth_port']}")
                    click.echo(f"  Auth DB: {status['auth_db']}")
                    click.echo(
                        f"  JWT Keys: {'Present' if status['jwt_keys_exist'] else 'Missing'}"
                    )
                    click.echo(f"  Created: {status['created_at']}")

                    click.echo("\n  Providers:")
                    providers = status["providers"]
                    click.echo(
                        f"    Email/Password: {'Enabled' if providers.get('email') else 'Disabled'}"
                    )
                    ml = "Enabled" if providers.get("magic_link") else "Disabled"
                    click.echo(f"    Magic Links:    {ml}")
                    anon = "Enabled" if providers.get("anonymous") else "Disabled"
                    click.echo(f"    Anonymous:      {anon}")
                    google = "Configured" if providers.get("google") else "Not configured"
                    click.echo(f"    Google OAuth:   {google}")
                    apple = "Configured" if providers.get("apple") else "Not configured"
                    click.echo(f"    Apple Sign-In:  {apple}")
        else:
            # All projects summary
            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Auth status for {status['total']} project(s)",
                    data=status,
                )
            else:
                click.echo("\nAuthentication Status")
                click.echo("-" * 70)
                click.echo(f"{'PROJECT':<20} {'STATUS':<10} {'PORT':<8} {'PROVIDERS':<30}")
                click.echo("-" * 70)

                for proj_status in status["projects"]:
                    name = proj_status["project"]
                    enabled = "Enabled" if proj_status["enabled"] else "Disabled"
                    port = str(proj_status["auth_port"]) if proj_status["auth_port"] else "-"

                    if proj_status["enabled"]:
                        providers = proj_status["providers"]
                        provider_list = []
                        if providers.get("email"):
                            provider_list.append("email")
                        if providers.get("google"):
                            provider_list.append("google")
                        if providers.get("apple"):
                            provider_list.append("apple")
                        providers_str = ", ".join(provider_list) if provider_list else "none"
                    else:
                        providers_str = "-"

                    click.echo(f"{name:<20} {enabled:<10} {port:<8} {providers_str:<30}")

                click.echo("-" * 70)
                click.echo(
                    f"Total: {status['total']} project(s), "
                    f"{status['auth_enabled_count']} with auth enabled"
                )

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("users")
@click.argument("project")
@click.option("--limit", "-n", default=50, help="Number of users to show (default: 50)")
@click.option("--verified-only", is_flag=True, help="Show only email-verified users")
@click.option("--provider", help="Filter by auth provider (email, google, apple, anonymous)")
@click.pass_context
@project_access("project")
def auth_users(
    ctx: click.Context,
    project: str,
    limit: int,
    verified_only: bool,
    provider: str | None,
) -> None:
    """List users for a project's auth service.

    Shows all users registered through the authentication service,
    including their email, verification status, and linked OAuth providers.

    Example:
        hostkit auth users myapp
        hostkit auth users myapp --limit 100
        hostkit auth users myapp --verified-only
        hostkit auth users myapp --provider google
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        users = service.list_auth_users(
            project=project,
            limit=limit,
            verified_only=verified_only,
            provider=provider,
        )

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Found {len(users)} user(s) for '{project}'",
                data={"users": users, "count": len(users)},
            )
        else:
            click.echo(f"\nAuth Users: {project}")
            click.echo("-" * 90)
            click.echo(f"{'ID':<36} {'EMAIL':<30} {'VERIFIED':<10} {'PROVIDERS':<14}")
            click.echo("-" * 90)

            for user in users:
                user_id = user["id"]
                email = user.get("email") or "(anonymous)"
                if len(email) > 28:
                    email = email[:25] + "..."
                verified = "Yes" if user.get("email_verified") else "No"
                providers = ", ".join(user.get("providers", [])) or "email"

                click.echo(f"{user_id:<36} {email:<30} {verified:<10} {providers:<14}")

            click.echo("-" * 90)
            click.echo(f"Total: {len(users)} user(s)")

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def auth_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View auth service logs for a project.

    Shows logs from the project's authentication service.
    Use --follow to stream logs in real-time.

    Example:
        hostkit auth logs myapp
        hostkit auth logs myapp --lines 50
        hostkit auth logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        # Verify auth is enabled for project
        if not service.auth_is_enabled(project):
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        service_name = f"hostkit-{project}-auth"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following auth logs for {project}... (Ctrl+C to stop)")
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
                    message=f"Auth logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nAuth logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("sync")
@click.argument("project")
@click.pass_context
@project_access("project")
def auth_sync(ctx: click.Context, project: str) -> None:
    """Sync auth environment variables for a project.

    Updates the project's .env file with the correct AUTH_URL and
    NEXT_PUBLIC_AUTH_URL values. Use this after HostKit updates or
    if your .env is missing these variables.

    AUTH_URL is set to the internal localhost URL for server-side calls.
    NEXT_PUBLIC_AUTH_URL is set to the external domain URL for client-side.

    Example:
        hostkit auth sync myapp
    """
    from pathlib import Path

    from hostkit.database import get_db

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        # Verify auth is enabled
        if not service.auth_is_enabled(project):
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        # Get auth config
        config = service.get_auth_config(project)
        if not config:
            raise AuthServiceError(
                code="AUTH_CONFIG_NOT_FOUND",
                message=f"Cannot read auth configuration for '{project}'",
                suggestion="Try disabling and re-enabling auth",
            )

        auth_port = config.port
        auth_url = f"http://127.0.0.1:{auth_port}"

        # Get external URL from domains
        db = get_db()
        domains = db.list_domains(project)
        next_public_auth_url = ""
        if domains:
            primary_domain = domains[0]["domain"]
            protocol = "https" if domains[0].get("ssl_enabled") else "http"
            next_public_auth_url = f"{protocol}://{primary_domain}"

        # Update .env file
        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            raise AuthServiceError(
                code="ENV_NOT_FOUND",
                message=f"Environment file not found: {env_path}",
                suggestion="Deploy the project first",
            )

        with open(env_path) as f:
            lines = f.readlines()

        updated_vars = []
        new_lines = []
        has_auth_url = False
        has_next_public = False

        for line in lines:
            if line.startswith("AUTH_URL="):
                new_lines.append(f"AUTH_URL={auth_url}\n")
                has_auth_url = True
                updated_vars.append("AUTH_URL")
            elif line.startswith("NEXT_PUBLIC_AUTH_URL="):
                if next_public_auth_url:
                    new_lines.append(f"NEXT_PUBLIC_AUTH_URL={next_public_auth_url}\n")
                    updated_vars.append("NEXT_PUBLIC_AUTH_URL")
                else:
                    new_lines.append(line)
                has_next_public = True
            else:
                new_lines.append(line)

        # Add missing variables
        if not has_auth_url:
            # Find AUTH_ENABLED line and add after it
            for i, line in enumerate(new_lines):
                if line.startswith("AUTH_ENABLED="):
                    new_lines.insert(i + 1, f"AUTH_URL={auth_url}\n")
                    updated_vars.append("AUTH_URL")
                    break

        if not has_next_public and next_public_auth_url:
            # Find AUTH_URL line and add after it
            for i, line in enumerate(new_lines):
                if line.startswith("AUTH_URL="):
                    new_lines.insert(i + 1, f"NEXT_PUBLIC_AUTH_URL={next_public_auth_url}\n")
                    updated_vars.append("NEXT_PUBLIC_AUTH_URL")
                    break

        with open(env_path, "w") as f:
            f.writelines(new_lines)

        # Set ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

        formatter.success(
            message=f"Synced auth environment variables for '{project}'",
            data={
                "project": project,
                "auth_url": auth_url,
                "next_public_auth_url": next_public_auth_url or None,
                "updated_vars": updated_vars,
            },
        )

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@auth.command("export-key")
@click.argument("project")
@click.option("--env-format", is_flag=True, help="Output in .env format with escaped newlines")
@click.option("--update-env", is_flag=True, help="Update project's .env file with inline key")
@click.pass_context
@project_access("project")
def auth_export_key(
    ctx: click.Context,
    project: str,
    env_format: bool,
    update_env: bool,
) -> None:
    """Export the JWT public key for a project.

    By default, outputs the raw PEM content.
    Use --env-format to get the key with escaped newlines for .env files.
    Use --update-env to update the project's .env file directly.

    This is useful for Next.js Edge runtime which cannot read files.

    Example:
        hostkit auth export-key myapp
        hostkit auth export-key myapp --env-format
        hostkit auth export-key myapp --update-env
    """
    from pathlib import Path

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = AuthService()

    try:
        # Verify auth is enabled
        if not service.auth_is_enabled(project):
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        # Get the public key path
        auth_dir = Path(f"/home/{project}/.auth")
        public_key_path = auth_dir / "jwt_public.pem"

        if not public_key_path.exists():
            raise AuthServiceError(
                code="KEY_NOT_FOUND",
                message=f"Public key not found at {public_key_path}",
                suggestion=(
                    f"Re-enable auth to regenerate keys:"
                    f" hostkit auth disable {project} --force"
                    f" && hostkit auth enable {project}"
                ),
            )

        # Read the key content
        pem_content = public_key_path.read_text().strip()

        if update_env:
            # Update the .env file
            env_path = Path(f"/home/{project}/.env")
            if not env_path.exists():
                raise AuthServiceError(
                    code="ENV_NOT_FOUND",
                    message=f"Environment file not found: {env_path}",
                    suggestion="Create project first with 'hostkit project create'",
                )

            # Escape newlines for .env format
            escaped_key = pem_content.replace("\n", "\\n")

            # Read and update .env
            with open(env_path) as f:
                lines = f.readlines()

            updated = False
            new_lines = []
            for line in lines:
                if line.startswith("AUTH_JWT_PUBLIC_KEY="):
                    new_lines.append(f'AUTH_JWT_PUBLIC_KEY="{escaped_key}"\n')
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                # Add the key if not present
                comment = "# JWT Public Key (inline for Edge runtime)"
                new_lines.append(f'\n{comment}\nAUTH_JWT_PUBLIC_KEY="{escaped_key}"\n')

            with open(env_path, "w") as f:
                f.writelines(new_lines)

            formatter.success(
                message=f"Updated {env_path} with inline JWT public key",
                data={
                    "project": project,
                    "env_file": str(env_path),
                    "key_format": "inline",
                },
            )

        elif env_format:
            # Output escaped for .env
            escaped_key = pem_content.replace("\n", "\\n")
            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"JWT public key for '{project}' (env format)",
                    data={
                        "project": project,
                        "public_key": escaped_key,
                        "format": "env",
                    },
                )
            else:
                click.echo(f'AUTH_JWT_PUBLIC_KEY="{escaped_key}"')

        else:
            # Output raw PEM
            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"JWT public key for '{project}'",
                    data={
                        "project": project,
                        "public_key": pem_content,
                        "format": "pem",
                    },
                )
            else:
                click.echo(pem_content)

    except AuthServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)
