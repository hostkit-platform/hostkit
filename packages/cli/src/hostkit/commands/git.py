"""Git configuration commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.git_service import GitService, GitServiceError


@click.group("git")
def git():
    """Git repository configuration for projects."""
    pass


@git.command("config")
@click.argument("project")
@click.option(
    "--repo",
    "-r",
    "repo_url",
    default=None,
    help="Git repository URL (https:// or git@)",
)
@click.option(
    "--branch",
    "-b",
    "default_branch",
    default=None,
    help="Default branch for deployments",
)
@click.option(
    "--ssh-key",
    default=None,
    help="Path to SSH private key for private repos",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show current configuration",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clear git configuration",
)
@click.pass_context
@project_owner("project")
def config(
    ctx,
    project: str,
    repo_url: str | None,
    default_branch: str | None,
    ssh_key: str | None,
    show: bool,
    clear: bool,
):
    """
    Configure git settings for a project.

    Examples:

        # Set repository URL
        hostkit git config myapp --repo https://github.com/user/repo.git

        # Set repository and default branch
        hostkit git config myapp --repo git@github.com:user/repo.git --branch main

        # Show current configuration
        hostkit git config myapp --show

        # Clear configuration
        hostkit git config myapp --clear
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = GitService()

    if clear:
        # Clear configuration
        try:
            if service.clear_project_config(project):
                service.clear_cache(project)
                formatter.success(
                    {"project": project, "cleared": True},
                    message=f"Git configuration cleared for '{project}'",
                )
            else:
                formatter.success(
                    {"project": project, "cleared": False},
                    message=f"No git configuration found for '{project}'",
                )
        except GitServiceError as e:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
            raise SystemExit(1)
        return

    if show or (repo_url is None and default_branch is None and ssh_key is None):
        # Show current configuration
        try:
            git_config = service.get_project_config(project)
            if git_config:
                data = {
                    "project": project,
                    "configured": True,
                    "repo_url": git_config.repo_url,
                    "default_branch": git_config.default_branch,
                    "ssh_key_path": git_config.ssh_key_path,
                    "created_at": git_config.created_at,
                    "updated_at": git_config.updated_at,
                }
                formatter.success(data, message=f"Git configuration for '{project}'")
            else:
                data = {
                    "project": project,
                    "configured": False,
                }
                formatter.success(data, message=f"No git configuration for '{project}'")
        except GitServiceError as e:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
            raise SystemExit(1)
        return

    # Set/update configuration
    if repo_url is None:
        # Check if we have existing config
        existing = service.get_project_config(project)
        if existing:
            repo_url = existing.repo_url
        else:
            formatter.error(
                code="REPO_URL_REQUIRED",
                message="Repository URL is required when setting initial configuration",
                suggestion="Use --repo to specify the git repository URL",
            )
            raise SystemExit(1)

    try:
        git_config = service.configure_project(
            project=project,
            repo_url=repo_url,
            default_branch=default_branch or "main",
            ssh_key_path=ssh_key,
        )
        data = {
            "project": project,
            "repo_url": git_config.repo_url,
            "default_branch": git_config.default_branch,
            "ssh_key_path": git_config.ssh_key_path,
        }
        formatter.success(data, message=f"Git configured for '{project}'")
    except GitServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@git.command("cache")
@click.option(
    "--clear",
    "-c",
    "clear_project",
    default=None,
    help="Clear cache for a specific project",
)
@click.option(
    "--list",
    "list_cache",
    is_flag=True,
    help="List cached repositories",
)
@click.pass_context
def cache(ctx, clear_project: str | None, list_cache: bool):
    """
    Manage git repository cache.

    Examples:

        # List cached repositories
        hostkit git cache --list

        # Clear cache for a project
        hostkit git cache --clear myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = GitService()

    if clear_project:
        if service.clear_cache(clear_project):
            formatter.success(
                {"project": clear_project, "cleared": True},
                message=f"Git cache cleared for '{clear_project}'",
            )
        else:
            formatter.success(
                {"project": clear_project, "cleared": False},
                message=f"No cache found for '{clear_project}'",
            )
        return

    # List cached repositories
    cached = service.list_cached_repos()
    if cached:
        data = {
            "count": len(cached),
            "repos": cached,
            "total_size_bytes": sum(r["size_bytes"] for r in cached),
        }
        formatter.success(data, message=f"Found {len(cached)} cached repositories")
    else:
        formatter.success({"count": 0, "repos": []}, message="No cached repositories")
