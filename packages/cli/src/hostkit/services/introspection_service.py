"""Click CLI introspection service for HostKit.

Walks the Click CLI tree and extracts command/subcommand/parameter metadata
for the `hostkit capabilities` command.
"""

from typing import Any

import click


def introspect_cli(cli_group: click.Group) -> dict[str, Any]:
    """Introspect a Click CLI group and extract all command metadata.

    Args:
        cli_group: The root Click group to introspect

    Returns:
        Dictionary mapping command names to their metadata
    """
    commands: dict[str, Any] = {}

    for name, cmd in cli_group.commands.items():
        commands[name] = introspect_command(cmd)

    return commands


def introspect_command(cmd: click.Command) -> dict[str, Any]:
    """Introspect a single Click command and extract its metadata.

    Args:
        cmd: Click command to introspect

    Returns:
        Dictionary containing command metadata:
            - name: Command name
            - help: Help text from docstring or cmd.help
            - type: "group" or "command"
            - params: List of parameter metadata
            - subcommands: Dictionary of subcommands (only if type is "group")
    """
    # Extract help text (prefer docstring over cmd.help)
    help_text = None
    if cmd.help:
        help_text = cmd.help.strip()
    elif cmd.callback and cmd.callback.__doc__:
        # Get first line of docstring
        help_text = cmd.callback.__doc__.strip().split("\n")[0]

    # Determine command type
    is_group = isinstance(cmd, click.Group)
    cmd_type = "group" if is_group else "command"

    # Extract parameters
    params = [introspect_parameter(param) for param in cmd.params]

    # Build result
    result: dict[str, Any] = {
        "name": cmd.name,
        "help": help_text,
        "type": cmd_type,
        "params": params,
    }

    # If this is a group, recursively introspect subcommands
    if is_group:
        subcommands: dict[str, Any] = {}
        for name, subcmd in cmd.commands.items():
            subcommands[name] = introspect_command(subcmd)
        result["subcommands"] = subcommands

    return result


def introspect_parameter(param: click.Parameter) -> dict[str, Any]:
    """Introspect a Click parameter and extract its metadata.

    Args:
        param: Click parameter to introspect

    Returns:
        Dictionary containing parameter metadata:
            - name: Parameter name
            - param_type: "argument" or "option"
            - type: Type name as string
            - required: Whether parameter is required
            - default: Default value (for options)
            - help: Help text (for options)
            - is_flag: Whether this is a flag (for options)
            - flag_value: Value when flag is set (for flag options)
            - multiple: Whether parameter accepts multiple values
            - choices: List of valid choices (for Choice parameters)
            - nargs: Number of arguments (for arguments)
    """
    is_argument = isinstance(param, click.Argument)
    param_type = "argument" if is_argument else "option"

    result: dict[str, Any] = {
        "name": param.name,
        "param_type": param_type,
        "type": _get_type_name(param.type),
        "required": param.required,
    }

    # Options-specific fields
    if not is_argument:
        result["default"] = param.default
        result["help"] = param.help
        result["is_flag"] = param.is_flag
        result["multiple"] = param.multiple

        # Flag value if this is a flag option
        if param.is_flag and hasattr(param, "flag_value"):
            result["flag_value"] = param.flag_value

    # Arguments-specific fields
    else:
        result["nargs"] = param.nargs

    # Extract choices if this is a Choice parameter
    if isinstance(param.type, click.Choice):
        result["choices"] = param.type.choices

    return result


def _get_type_name(param_type: click.ParamType) -> str:
    """Get a string representation of a Click parameter type.

    Args:
        param_type: Click parameter type

    Returns:
        Human-readable type name
    """
    if isinstance(param_type, click.Choice):
        return f"choice({','.join(param_type.choices)})"
    elif isinstance(param_type, click.IntRange):
        return f"int(min={param_type.min},max={param_type.max})"
    elif isinstance(param_type, click.FloatRange):
        return f"float(min={param_type.min},max={param_type.max})"
    elif isinstance(param_type, click.Path):
        attrs = []
        if param_type.exists:
            attrs.append("exists=True")
        if param_type.file_okay:
            attrs.append("file_ok")
        if param_type.dir_okay:
            attrs.append("dir_ok")
        attr_str = ",".join(attrs) if attrs else ""
        return f"path({attr_str})" if attr_str else "path"
    elif isinstance(param_type, click.File):
        return f"file({param_type.mode})"
    else:
        # Use the type's name property or class name as fallback
        # Note: click.STRING, click.INT etc. are instances, not types,
        # so we use param_type.name which is a standard Click property
        return getattr(param_type, "name", param_type.__class__.__name__).lower()
