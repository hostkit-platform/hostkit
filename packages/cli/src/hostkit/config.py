"""Configuration management for HostKit."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HostKitConfig:
    """HostKit configuration settings."""

    # Paths
    data_dir: Path = field(default_factory=lambda: Path("/var/lib/hostkit"))
    log_dir: Path = field(default_factory=lambda: Path("/var/log/hostkit"))
    backup_dir: Path = field(default_factory=lambda: Path("/backups"))
    config_file: Path = field(default_factory=lambda: Path("/etc/hostkit/config.yaml"))
    templates_dir: Path = field(default_factory=lambda: Path("/var/lib/hostkit/templates"))

    # Database
    db_path: Path = field(default_factory=lambda: Path("/var/lib/hostkit/hostkit.db"))

    # Services
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    redis_host: str = "localhost"
    redis_port: int = 6379
    nginx_sites_available: Path = field(
        default_factory=lambda: Path("/etc/nginx/sites-available")
    )
    nginx_sites_enabled: Path = field(
        default_factory=lambda: Path("/etc/nginx/sites-enabled")
    )

    # Project defaults
    default_runtime: str = "python"
    base_port: int = 8000
    max_projects: int = 50

    # SSL/Let's Encrypt
    admin_email: str | None = field(default_factory=lambda: os.environ.get("HOSTKIT_ADMIN_EMAIL"))

    # VPS IP — used for nip.io dev domains, DNS validation, capabilities
    vps_ip: str = field(default_factory=lambda: os.environ.get("HOSTKIT_VPS_IP", ""))

    # Operator SSH keys - automatically added to all new projects
    operator_ssh_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Convert string paths to Path objects if needed."""
        path_fields = [
            "data_dir",
            "log_dir",
            "backup_dir",
            "config_file",
            "templates_dir",
            "db_path",
            "nginx_sites_available",
            "nginx_sites_enabled",
        ]
        for field_name in path_fields:
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, Path(value))

    @classmethod
    def load(cls, config_path: Path | None = None) -> "HostKitConfig":
        """Load configuration from YAML file, falling back to defaults."""
        config = cls()

        if config_path is None:
            config_path = config.config_file

        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = yaml.safe_load(f) or {}
                config = cls._from_dict(data)
            except (yaml.YAMLError, OSError):
                pass  # Fall back to defaults

        return config

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "HostKitConfig":
        """Create config from dictionary."""
        kwargs: dict[str, Any] = {}

        # Map YAML keys to dataclass fields
        mappings = {
            "data_dir": "data_dir",
            "log_dir": "log_dir",
            "backup_dir": "backup_dir",
            "templates_dir": "templates_dir",
            "db_path": "db_path",
            "postgres_host": "postgres_host",
            "postgres_port": "postgres_port",
            "redis_host": "redis_host",
            "redis_port": "redis_port",
            "nginx_sites_available": "nginx_sites_available",
            "nginx_sites_enabled": "nginx_sites_enabled",
            "default_runtime": "default_runtime",
            "base_port": "base_port",
            "max_projects": "max_projects",
            "admin_email": "admin_email",
            "vps_ip": "vps_ip",
            "operator_ssh_keys": "operator_ssh_keys",
        }

        for yaml_key, field_name in mappings.items():
            if yaml_key in data:
                kwargs[field_name] = data[yaml_key]

        return cls(**kwargs)

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        dirs = [self.data_dir, self.log_dir, self.backup_dir]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)


# Global config instance (loaded lazily)
_config: HostKitConfig | None = None


def get_config() -> HostKitConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = HostKitConfig.load()
    return _config


def reload_config() -> HostKitConfig:
    """Reload configuration from file."""
    global _config
    _config = HostKitConfig.load()
    return _config
