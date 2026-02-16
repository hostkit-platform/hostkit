"""Runtime detection utility for HostKit projects.

Detects project runtime from file system contents. Used by both
the provision command and MCP deploy_local auto-provisioning.
"""

from pathlib import Path


def detect_runtime(path: str | Path) -> str:
    """Detect runtime from project files.

    Priority order:
        1. next.config.* -> nextjs
        2. package.json (no next.config) -> node
        3. requirements.txt or pyproject.toml -> python
        4. index.html (no package.json) -> static
        5. fallback -> nextjs

    Args:
        path: Path to the project directory.

    Returns:
        One of: 'nextjs', 'node', 'python', 'static'
    """
    p = Path(path)

    if not p.is_dir():
        return "nextjs"

    # Check for Next.js config files
    has_next_config = (
        (p / "next.config.js").exists()
        or (p / "next.config.mjs").exists()
        or (p / "next.config.ts").exists()
    )
    if has_next_config:
        return "nextjs"

    has_package_json = (p / "package.json").exists()

    # Node.js project without Next.js
    if has_package_json:
        return "node"

    # Python project
    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists():
        return "python"

    # Static site (has index.html but no package.json)
    if (p / "index.html").exists():
        return "static"

    # Default to nextjs for AI agent deployments
    return "nextjs"
