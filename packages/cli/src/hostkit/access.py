"""User-level access control for HostKit CLI.

This module provides access control for running HostKit commands at the
project user level (non-root) rather than requiring root access for all
operations.

Access Levels:
- ROOT: Full access to all commands
- PROJECT_USER: Access only to commands for their own project

Commands are categorized as:
- ROOT_ONLY: Requires root (system-level operations)
- PROJECT_SCOPED: Can run at user level for own project
- READ_ONLY: Safe read operations that any project user can run
"""

import functools
import os
import pwd
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

import click

from hostkit.database import get_db


class AccessLevel(Enum):
    """Access levels for HostKit operations."""
    ROOT = "root"
    OPERATOR = "operator"  # AI agent with controlled sudo access
    PROJECT_USER = "project_user"
    UNKNOWN = "unknown"


class CommandScope(Enum):
    """Scope of commands - determines who can run them."""
    ROOT_ONLY = "root_only"          # Only root can run
    PROJECT_SCOPED = "project_scoped"  # Project user can run for own project
    PROJECT_READ = "project_read"     # Read-only for own project
    GLOBAL_READ = "global_read"       # Any user can read (system status)


@dataclass
class AccessContext:
    """Current user's access context."""
    level: AccessLevel
    username: str
    uid: int
    project: str | None  # None if root, operator, or unknown user

    @property
    def is_root(self) -> bool:
        return self.level == AccessLevel.ROOT

    @property
    def is_operator(self) -> bool:
        return self.level == AccessLevel.OPERATOR

    @property
    def is_project_user(self) -> bool:
        return self.level == AccessLevel.PROJECT_USER and self.project is not None


class AccessDeniedError(Exception):
    """Raised when access is denied to a resource."""

    def __init__(self, message: str, suggestion: str | None = None):
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion


def get_access_context() -> AccessContext:
    """Determine the current user's access context.

    Handles sudo invocation by checking SUDO_USER to identify operators.

    Returns:
        AccessContext with level, username, uid, and optional project name
    """
    uid = os.getuid()
    db = get_db()

    # Check if running via sudo (uid is 0 but SUDO_USER is set)
    sudo_user = os.environ.get("SUDO_USER")

    # Root user - but check if actually an operator running via sudo
    if uid == 0:
        if sudo_user and sudo_user != "root":
            # Check if SUDO_USER is an operator
            operator = db.get_operator(sudo_user)
            if operator:
                return AccessContext(
                    level=AccessLevel.OPERATOR,
                    username=sudo_user,
                    uid=0,  # Running as root
                    project=None,
                )
        # Actual root user
        return AccessContext(
            level=AccessLevel.ROOT,
            username="root",
            uid=0,
            project=None,
        )

    # Non-root user - check if they're an operator or project user
    try:
        pw_info = pwd.getpwuid(uid)
        username = pw_info.pw_name
    except KeyError:
        return AccessContext(
            level=AccessLevel.UNKNOWN,
            username="unknown",
            uid=uid,
            project=None,
        )

    # Check if user is an operator (AI agent with controlled sudo)
    operator = db.get_operator(username)
    if operator:
        return AccessContext(
            level=AccessLevel.OPERATOR,
            username=username,
            uid=uid,
            project=None,
        )

    # Check if username corresponds to a valid project
    project = db.get_project(username)

    if project:
        return AccessContext(
            level=AccessLevel.PROJECT_USER,
            username=username,
            uid=uid,
            project=username,
        )

    # User exists but isn't an operator or project user
    return AccessContext(
        level=AccessLevel.UNKNOWN,
        username=username,
        uid=uid,
        project=None,
    )


def require_root() -> None:
    """Check that current user is root. Raises AccessDeniedError if not."""
    ctx = get_access_context()
    if not ctx.is_root:
        raise AccessDeniedError(
            message="This command requires root privileges",
            suggestion="Run as root: sudo hostkit <command>",
        )


def require_operator_or_root() -> None:
    """Check that current user is root or an operator. Raises AccessDeniedError if not."""
    ctx = get_access_context()
    if not ctx.is_root and not ctx.is_operator:
        raise AccessDeniedError(
            message="This command requires root or operator privileges",
            suggestion="Run as root: sudo hostkit <command>",
        )


def require_project_owner(project_name: str) -> None:
    """Check that current user owns or can access the specified project.

    Access is granted if:
    - User is root (can access everything)
    - User is the operator who created the project
    - User is the project user (username matches project name)

    Args:
        project_name: The project to check ownership/access for

    Raises:
        AccessDeniedError: If user doesn't have access
    """
    ctx = get_access_context()

    # Root can access everything
    if ctx.is_root:
        return

    # Project user can only access their own project
    if ctx.is_project_user and ctx.project == project_name:
        return

    # Operators have full access to all projects (they have sudo access anyway)
    if ctx.is_operator:
        return

    # Access denied
    if ctx.is_project_user:
        raise AccessDeniedError(
            message=f"Access denied: you can only access project '{ctx.project}'",
            suggestion=f"You attempted to access '{project_name}'",
        )
    else:
        raise AccessDeniedError(
            message=f"Access denied to project '{project_name}'",
            suggestion="Run as root or as the project user",
        )


def require_project_access(project_name: str) -> None:
    """Check that current user can access the specified project.

    Access is granted if:
    - User is root (can access everything)
    - User is the operator who created the project
    - User is the project user (username matches project name)

    Args:
        project_name: The project to check access for

    Raises:
        AccessDeniedError: If user doesn't have access
    """
    ctx = get_access_context()

    # Root can access everything
    if ctx.is_root:
        return

    # Project user can only access their own project
    if ctx.is_project_user and ctx.project == project_name:
        return

    # Operators have full access to all projects (they have sudo access anyway)
    if ctx.is_operator:
        return

    # Access denied
    if ctx.is_project_user:
        raise AccessDeniedError(
            message=f"Access denied: you can only access project '{ctx.project}'",
            suggestion=f"You attempted to access '{project_name}'",
        )
    else:
        raise AccessDeniedError(
            message=f"Access denied to project '{project_name}'",
            suggestion="Run as root or as the project user",
        )


def require_project_write(project_name: str) -> None:
    """Check that current user can modify the specified project.

    For now, same as require_project_access. In the future, could add
    more granular permissions.
    """
    require_project_access(project_name)


# Type variable for decorator
F = TypeVar("F", bound=Callable)


def root_only(func: F) -> F:
    """Decorator: Command requires root access."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            require_root()
        except AccessDeniedError as e:
            # Get formatter from click context if available
            ctx = click.get_current_context(silent=True)
            if ctx and "formatter" in ctx.obj:
                ctx.obj["formatter"].error(
                    code="ACCESS_DENIED",
                    message=e.message,
                    suggestion=e.suggestion,
                )
                raise SystemExit(1)
            else:
                raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
        return func(*args, **kwargs)
    return wrapper  # type: ignore


def operator_or_root(func: F) -> F:
    """Decorator: Command requires root or operator access."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            require_operator_or_root()
        except AccessDeniedError as e:
            # Get formatter from click context if available
            ctx = click.get_current_context(silent=True)
            if ctx and "formatter" in ctx.obj:
                ctx.obj["formatter"].error(
                    code="ACCESS_DENIED",
                    message=e.message,
                    suggestion=e.suggestion,
                )
                raise SystemExit(1)
            else:
                raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
        return func(*args, **kwargs)
    return wrapper  # type: ignore


def project_owner(project_arg: str = "project") -> Callable[[F], F]:
    """Decorator factory: Command requires ownership of the specified project.

    This is used for operators who can only access projects they created.
    Root can access any project.
    Project users can only access their own project (by name match).

    Args:
        project_arg: Name of the argument containing the project name (default: "project")
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get project name from kwargs or args
            project_name = kwargs.get(project_arg)
            if project_name is None:
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if project_arg in params:
                    idx = params.index(project_arg)
                    if idx < len(args):
                        project_name = args[idx]

            if project_name is None:
                raise click.ClickException(f"Could not determine project name from '{project_arg}' argument")

            try:
                require_project_owner(project_name)
            except AccessDeniedError as e:
                ctx = click.get_current_context(silent=True)
                if ctx and ctx.obj and "formatter" in ctx.obj:
                    ctx.obj["formatter"].error(
                        code="ACCESS_DENIED",
                        message=e.message,
                        suggestion=e.suggestion,
                    )
                    raise SystemExit(1)
                else:
                    raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator


def project_access(project_arg: str = "project") -> Callable[[F], F]:
    """Decorator factory: Command requires access to the specified project.

    Args:
        project_arg: Name of the argument containing the project name (default: "project")

    Usage:
        @project_access()  # Uses 'project' argument
        def my_command(project: str): ...

        @project_access("name")  # Uses 'name' argument
        def my_command(name: str): ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get project name from kwargs or args
            project_name = kwargs.get(project_arg)
            if project_name is None:
                # Try to get from positional args based on function signature
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if project_arg in params:
                    idx = params.index(project_arg)
                    if idx < len(args):
                        project_name = args[idx]

            if project_name is None:
                raise click.ClickException(f"Could not determine project name from '{project_arg}' argument")

            try:
                require_project_access(project_name)
            except AccessDeniedError as e:
                ctx = click.get_current_context(silent=True)
                if ctx and ctx.obj and "formatter" in ctx.obj:
                    ctx.obj["formatter"].error(
                        code="ACCESS_DENIED",
                        message=e.message,
                        suggestion=e.suggestion,
                    )
                    raise SystemExit(1)
                else:
                    raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator


def project_write(project_arg: str = "project") -> Callable[[F], F]:
    """Decorator factory: Command requires write access to the specified project."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            project_name = kwargs.get(project_arg)
            if project_name is None:
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if project_arg in params:
                    idx = params.index(project_arg)
                    if idx < len(args):
                        project_name = args[idx]

            if project_name is None:
                raise click.ClickException(f"Could not determine project name from '{project_arg}' argument")

            try:
                require_project_write(project_name)
            except AccessDeniedError as e:
                ctx = click.get_current_context(silent=True)
                if ctx and ctx.obj and "formatter" in ctx.obj:
                    ctx.obj["formatter"].error(
                        code="ACCESS_DENIED",
                        message=e.message,
                        suggestion=e.suggestion,
                    )
                    raise SystemExit(1)
                else:
                    raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator


# Mapping of command paths to their required scope
# This is used for documentation and could be used for runtime checks
COMMAND_SCOPES: dict[str, CommandScope] = {
    # Root-only commands (system level)
    "project create": CommandScope.ROOT_ONLY,
    "project delete": CommandScope.ROOT_ONLY,
    "nginx add": CommandScope.ROOT_ONLY,
    "nginx remove": CommandScope.ROOT_ONLY,
    "nginx test": CommandScope.ROOT_ONLY,
    "ssl provision": CommandScope.ROOT_ONLY,
    "dns add": CommandScope.ROOT_ONLY,
    "dns remove": CommandScope.ROOT_ONLY,
    "mail add-domain": CommandScope.ROOT_ONLY,
    "mail add-address": CommandScope.ROOT_ONLY,
    "mail setup": CommandScope.ROOT_ONLY,
    "mail remove-domain": CommandScope.ROOT_ONLY,
    # Project-scoped mail commands
    "mail enable": CommandScope.PROJECT_SCOPED,
    "mail disable": CommandScope.PROJECT_SCOPED,
    "mail add": CommandScope.PROJECT_SCOPED,
    "mail remove": CommandScope.PROJECT_SCOPED,
    "mail list": CommandScope.PROJECT_READ,
    "mail credentials": CommandScope.PROJECT_SCOPED,
    "mail send-test": CommandScope.PROJECT_SCOPED,
    # Payment service commands (project-scoped)
    "payments enable": CommandScope.PROJECT_SCOPED,
    "payments disable": CommandScope.PROJECT_SCOPED,
    "payments status": CommandScope.PROJECT_READ,
    "payments logs": CommandScope.PROJECT_READ,
    # SMS service commands (project-scoped)
    "sms enable": CommandScope.PROJECT_SCOPED,
    "sms disable": CommandScope.PROJECT_SCOPED,
    "sms status": CommandScope.PROJECT_READ,
    "sms send": CommandScope.PROJECT_SCOPED,
    "sms template": CommandScope.PROJECT_SCOPED,
    "sms logs": CommandScope.PROJECT_READ,
    # Voice service commands (project-scoped)
    "voice enable": CommandScope.PROJECT_SCOPED,
    "voice disable": CommandScope.PROJECT_SCOPED,
    "voice status": CommandScope.PROJECT_READ,
    "voice agent": CommandScope.PROJECT_SCOPED,
    "voice call": CommandScope.PROJECT_SCOPED,
    "voice logs": CommandScope.PROJECT_READ,
    # Booking service commands (project-scoped)
    "booking enable": CommandScope.PROJECT_SCOPED,
    "booking disable": CommandScope.PROJECT_SCOPED,
    "booking status": CommandScope.PROJECT_READ,
    "booking seed": CommandScope.PROJECT_SCOPED,
    "booking logs": CommandScope.PROJECT_READ,
    # Chatbot service commands (project-scoped)
    "chatbot enable": CommandScope.PROJECT_SCOPED,
    "chatbot disable": CommandScope.PROJECT_SCOPED,
    "chatbot status": CommandScope.PROJECT_READ,
    "chatbot config": CommandScope.PROJECT_SCOPED,
    "chatbot stats": CommandScope.PROJECT_READ,
    "chatbot logs": CommandScope.PROJECT_READ,
    # Docs and query commands (system-level documentation)
    "docs index": CommandScope.ROOT_ONLY,
    "docs status": CommandScope.GLOBAL_READ,
    "query": CommandScope.GLOBAL_READ,  # Any user can query docs
    "storage create-bucket": CommandScope.ROOT_ONLY,
    "db create": CommandScope.ROOT_ONLY,
    "db delete": CommandScope.ROOT_ONLY,
    "redis create": CommandScope.ROOT_ONLY,
    "redis delete": CommandScope.ROOT_ONLY,
    "auth enable": CommandScope.ROOT_ONLY,
    "auth disable": CommandScope.ROOT_ONLY,
    "service create-worker": CommandScope.ROOT_ONLY,
    "service delete-worker": CommandScope.ROOT_ONLY,
    "log setup": CommandScope.ROOT_ONLY,
    "log setup-rotation": CommandScope.ROOT_ONLY,
    # Project-scoped write commands (project user can run for own project)
    "project start": CommandScope.PROJECT_SCOPED,
    "project stop": CommandScope.PROJECT_SCOPED,
    "project restart": CommandScope.PROJECT_SCOPED,
    "service start": CommandScope.PROJECT_SCOPED,
    "service stop": CommandScope.PROJECT_SCOPED,
    "service restart": CommandScope.PROJECT_SCOPED,
    "service enable": CommandScope.PROJECT_SCOPED,
    "service disable": CommandScope.PROJECT_SCOPED,
    "auth config": CommandScope.PROJECT_SCOPED,
    "db backup": CommandScope.PROJECT_SCOPED,
    "backup create": CommandScope.PROJECT_SCOPED,
    "log clear": CommandScope.PROJECT_SCOPED,

    # Project-scoped read commands (project user can read own project)
    "project info": CommandScope.PROJECT_READ,
    "service status": CommandScope.PROJECT_READ,
    "service logs": CommandScope.PROJECT_READ,
    "auth status": CommandScope.PROJECT_READ,
    "auth users": CommandScope.PROJECT_READ,
    "auth logs": CommandScope.PROJECT_READ,
    "log show": CommandScope.PROJECT_READ,
    "log search": CommandScope.PROJECT_READ,
    "log files": CommandScope.PROJECT_READ,
    "log stats": CommandScope.PROJECT_READ,
    "log export": CommandScope.PROJECT_READ,
    "db shell": CommandScope.PROJECT_READ,
    "db info": CommandScope.PROJECT_READ,
    "backup list": CommandScope.PROJECT_READ,
    "storage credentials": CommandScope.PROJECT_READ,

    # Global read commands (any user can run)
    "status": CommandScope.GLOBAL_READ,
    "project list": CommandScope.GLOBAL_READ,
    "service list": CommandScope.GLOBAL_READ,
    "db list": CommandScope.GLOBAL_READ,

    # SSH management commands
    "ssh add-key": CommandScope.PROJECT_SCOPED,
    "ssh remove-key": CommandScope.PROJECT_SCOPED,
    "ssh list-keys": CommandScope.PROJECT_READ,
    "ssh sessions": CommandScope.PROJECT_READ,
    "ssh kick": CommandScope.PROJECT_SCOPED,
    "ssh enable": CommandScope.ROOT_ONLY,
    "ssh disable": CommandScope.ROOT_ONLY,
    "ssh status": CommandScope.PROJECT_READ,

    # Deploy command - project users can deploy their own project
    "deploy": CommandScope.PROJECT_SCOPED,

    # Rollback command - project users can rollback their own project
    "rollback": CommandScope.PROJECT_SCOPED,

    # Health command - project users can check their own project health
    "health": CommandScope.PROJECT_READ,

    # Exec command - run arbitrary commands in project context
    "exec": CommandScope.PROJECT_SCOPED,

    # Migrate command - project users can run migrations on their own project
    "migrate": CommandScope.PROJECT_SCOPED,

    # Backup restore - project users can restore their own backups
    "backup restore": CommandScope.PROJECT_SCOPED,

    # R2 cloud backup commands (project-scoped)
    "backup r2 sync": CommandScope.PROJECT_SCOPED,
    "backup r2 list": CommandScope.PROJECT_READ,
    "backup r2 rotate": CommandScope.PROJECT_SCOPED,
    "backup r2 download": CommandScope.PROJECT_SCOPED,
    "backup r2 status": CommandScope.GLOBAL_READ,

    # Env command - project users can manage their own env vars
    "env": CommandScope.PROJECT_SCOPED,
    "env set": CommandScope.PROJECT_SCOPED,
    "env unset": CommandScope.PROJECT_SCOPED,
    "env import": CommandScope.PROJECT_SCOPED,
    "env sync": CommandScope.PROJECT_SCOPED,

    # Secrets portal - project users can generate magic links for their own project
    "secrets portal": CommandScope.PROJECT_SCOPED,

    # Provision command (operators and root)
    "provision": CommandScope.ROOT_ONLY,

    # Cron job commands (project-scoped)
    "cron add": CommandScope.PROJECT_SCOPED,
    "cron remove": CommandScope.PROJECT_SCOPED,
    "cron list": CommandScope.PROJECT_READ,
    "cron run": CommandScope.PROJECT_SCOPED,
    "cron logs": CommandScope.PROJECT_READ,
    "cron enable": CommandScope.PROJECT_SCOPED,
    "cron disable": CommandScope.PROJECT_SCOPED,
    "cron info": CommandScope.PROJECT_READ,

    # Worker commands (project-scoped)
    "worker add": CommandScope.PROJECT_SCOPED,
    "worker remove": CommandScope.PROJECT_SCOPED,
    "worker list": CommandScope.PROJECT_READ,
    "worker status": CommandScope.PROJECT_READ,
    "worker start": CommandScope.PROJECT_SCOPED,
    "worker stop": CommandScope.PROJECT_SCOPED,
    "worker restart": CommandScope.PROJECT_SCOPED,
    "worker scale": CommandScope.PROJECT_SCOPED,
    "worker logs": CommandScope.PROJECT_READ,
    "worker beat enable": CommandScope.PROJECT_SCOPED,
    "worker beat disable": CommandScope.PROJECT_SCOPED,
    "worker beat status": CommandScope.PROJECT_READ,
    "worker beat logs": CommandScope.PROJECT_READ,

    # Operator management commands (root only)
    "operator setup": CommandScope.ROOT_ONLY,
    "operator add-key": CommandScope.ROOT_ONLY,
    "operator test": CommandScope.ROOT_ONLY,
    "operator revoke": CommandScope.ROOT_ONLY,
    "operator list": CommandScope.ROOT_ONLY,

    # Vector service commands
    "vector setup": CommandScope.ROOT_ONLY,
    "vector status": CommandScope.GLOBAL_READ,
    "vector enable": CommandScope.PROJECT_SCOPED,
    "vector disable": CommandScope.PROJECT_SCOPED,
    "vector key": CommandScope.PROJECT_SCOPED,
    "vector collections": CommandScope.PROJECT_READ,
    "vector create-collection": CommandScope.PROJECT_SCOPED,
    "vector delete-collection": CommandScope.PROJECT_SCOPED,
    "vector collection-info": CommandScope.PROJECT_READ,
    "vector ingest": CommandScope.PROJECT_SCOPED,
    "vector search": CommandScope.PROJECT_READ,
    "vector jobs": CommandScope.PROJECT_READ,
    "vector job": CommandScope.PROJECT_READ,
    "vector usage": CommandScope.PROJECT_READ,

    # Image generation commands
    "image generate": CommandScope.PROJECT_SCOPED,
    "image models": CommandScope.GLOBAL_READ,
    "image usage": CommandScope.PROJECT_READ,
    "image history": CommandScope.PROJECT_READ,
    "image config": CommandScope.GLOBAL_READ,
    # R2 storage commands (project-scoped)
    "r2 enable": CommandScope.PROJECT_SCOPED,
    "r2 disable": CommandScope.PROJECT_SCOPED,
    "r2 status": CommandScope.PROJECT_READ,
    "r2 upload": CommandScope.PROJECT_SCOPED,
    "r2 download": CommandScope.PROJECT_READ,
    "r2 list": CommandScope.PROJECT_READ,
    "r2 delete": CommandScope.PROJECT_SCOPED,
    "r2 presign": CommandScope.PROJECT_READ,
    "r2 usage": CommandScope.GLOBAL_READ,
    "r2 credentials": CommandScope.PROJECT_READ,

    # Permissions management (root only - system-level)
    "permissions gaps": CommandScope.ROOT_ONLY,
    "permissions show": CommandScope.ROOT_ONLY,
    "permissions sync": CommandScope.ROOT_ONLY,
    "permissions verify": CommandScope.ROOT_ONLY,
}


def get_command_scope(command_path: str) -> CommandScope:
    """Get the scope for a command path.

    Args:
        command_path: Space-separated command path, e.g., "project create"

    Returns:
        CommandScope for the command, defaults to ROOT_ONLY if not found
    """
    return COMMAND_SCOPES.get(command_path, CommandScope.ROOT_ONLY)


def extract_project_from_service_name(name: str) -> str | None:
    """Extract project name from a service name.

    Service names follow patterns like:
    - "myapp" (project name directly)
    - "hostkit-myapp" (main app service)
    - "hostkit-myapp-auth" (auth service)
    - "hostkit-myapp-worker" (worker service)

    Args:
        name: Service name or project name

    Returns:
        Project name, or None if it can't be determined
    """
    # Check if it's a hostkit service name
    if name.startswith("hostkit-"):
        # Remove the prefix
        remainder = name[8:]  # len("hostkit-") = 8

        # Check for known suffixes
        for suffix in ["-auth", "-worker"]:
            if remainder.endswith(suffix):
                return remainder[:-len(suffix)]

        # No known suffix, the remainder is the project name
        return remainder

    # It might be a project name directly
    db = get_db()
    project = db.get_project(name)
    if project:
        return name

    return None


def service_access(name_arg: str = "name") -> Callable[[F], F]:
    """Decorator factory: Command requires access to the project owning a service.

    This handles service names like "hostkit-myapp" or "hostkit-myapp-auth"
    and extracts the project name to check access.

    Args:
        name_arg: Name of the argument containing the service/project name
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get service/project name from kwargs or args
            name = kwargs.get(name_arg)
            if name is None:
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if name_arg in params:
                    idx = params.index(name_arg)
                    if idx < len(args):
                        name = args[idx]

            if name is None:
                raise click.ClickException(f"Could not determine service name from '{name_arg}' argument")

            # Extract project name from service name
            project_name = extract_project_from_service_name(name)
            if project_name is None:
                # Can't determine project - require root
                try:
                    require_root()
                except AccessDeniedError as e:
                    ctx = click.get_current_context(silent=True)
                    if ctx and ctx.obj and "formatter" in ctx.obj:
                        ctx.obj["formatter"].error(
                            code="ACCESS_DENIED",
                            message=f"Cannot determine project for service '{name}'. {e.message}",
                            suggestion=e.suggestion,
                        )
                        raise SystemExit(1)
                    else:
                        raise click.ClickException(f"Cannot determine project for '{name}'. {e.message}")
            else:
                try:
                    require_project_access(project_name)
                except AccessDeniedError as e:
                    ctx = click.get_current_context(silent=True)
                    if ctx and ctx.obj and "formatter" in ctx.obj:
                        ctx.obj["formatter"].error(
                            code="ACCESS_DENIED",
                            message=e.message,
                            suggestion=e.suggestion,
                        )
                        raise SystemExit(1)
                    else:
                        raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator
