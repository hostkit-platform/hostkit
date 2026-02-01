"""Central registry for HostKit services and runtimes.

This module provides a central registry where services and runtimes self-register
their metadata. This enables the `hostkit capabilities` command to discover
available services without maintaining a separate YAML file.
"""

from dataclasses import dataclass, field


@dataclass
class ServiceMeta:
    """Metadata for a HostKit service.

    Attributes:
        name: Service name (e.g., "database", "auth", "mail")
        description: Human-readable description of the service
        provision_flag: Flag used during project creation (e.g., "--with-db")
        enable_command: Command to enable the service (e.g., "hostkit mail enable {project}")
        env_vars_provided: List of environment variables injected by this service
        related_commands: List of related commands for this service
    """

    name: str
    description: str
    provision_flag: str | None = None
    enable_command: str | None = None
    env_vars_provided: list[str] = field(default_factory=list)
    related_commands: list[str] = field(default_factory=list)


@dataclass
class RuntimeMeta:
    """Metadata for a HostKit runtime.

    Attributes:
        name: Runtime name (e.g., "python", "node", "nextjs", "static")
        flag: Flag used during project creation (e.g., "--runtime python")
        description: Human-readable description of the runtime
        start_command: Command to start the runtime (None for static)
        package_manager: Package manager used by this runtime (None for static)
    """

    name: str
    flag: str
    description: str
    start_command: str | None = None
    package_manager: str | None = None


class CapabilitiesRegistry:
    """Central registry for HostKit capabilities.

    Services and runtimes register themselves at module import time.
    This registry is then queried by the `hostkit capabilities` command
    to generate the capabilities manifest.
    """

    _services: dict[str, ServiceMeta] = {}
    _runtimes: dict[str, RuntimeMeta] = {}

    @classmethod
    def register_service(cls, meta: ServiceMeta) -> None:
        """Register a service with the capabilities registry.

        Args:
            meta: Service metadata to register
        """
        cls._services[meta.name] = meta

    @classmethod
    def register_runtime(cls, meta: RuntimeMeta) -> None:
        """Register a runtime with the capabilities registry.

        Args:
            meta: Runtime metadata to register
        """
        cls._runtimes[meta.name] = meta

    @classmethod
    def get_services(cls) -> dict[str, ServiceMeta]:
        """Get all registered services.

        Returns:
            Dictionary mapping service names to their metadata
        """
        return cls._services.copy()

    @classmethod
    def get_runtimes(cls) -> dict[str, RuntimeMeta]:
        """Get all registered runtimes.

        Returns:
            Dictionary mapping runtime names to their metadata
        """
        return cls._runtimes.copy()


# Register core runtimes at module level
CapabilitiesRegistry.register_runtime(
    RuntimeMeta(
        name="python",
        flag="--runtime python",
        description="Python with virtualenv",
        start_command="venv/bin/python -m app",
        package_manager="pip",
    )
)

CapabilitiesRegistry.register_runtime(
    RuntimeMeta(
        name="node",
        flag="--runtime node",
        description="Node.js application",
        start_command="node app/index.js",
        package_manager="npm",
    )
)

CapabilitiesRegistry.register_runtime(
    RuntimeMeta(
        name="nextjs",
        flag="--runtime nextjs",
        description="Next.js with SSR/SSG",
        start_command="npm start",
        package_manager="npm",
    )
)

CapabilitiesRegistry.register_runtime(
    RuntimeMeta(
        name="static",
        flag="--runtime static",
        description="Static files served by nginx",
        start_command=None,
        package_manager=None,
    )
)
