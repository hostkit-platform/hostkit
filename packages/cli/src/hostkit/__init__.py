"""HostKit - AI-agent-native VPS management CLI."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hostkit")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
