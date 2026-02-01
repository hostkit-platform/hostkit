"""Voice service management for HostKit.

Provides central voice calling service via Twilio Media Streams with
real-time streaming STT (Deepgram), TTS (Cartesia), and LLM (OpenAI).
"""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta


# Register Voice service with capabilities registry
CapabilitiesRegistry.register_service(ServiceMeta(
    name="voice",
    description="AI-powered phone calls via Twilio Media Streams (real-time STT/TTS/LLM streaming)",
    provision_flag="--with-voice",
    enable_command="hostkit voice enable {project}",
    env_vars_provided=["VOICE_API_KEY", "VOICE_API_URL", "VOICE_WEBHOOK_SECRET"],
    related_commands=["voice enable", "voice disable", "voice status", "voice agent", "voice call", "voice logs"],
))


class VoiceServiceError(Exception):
    """Base exception for voice service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_api_key(project: str) -> str:
    """Generate a voice API key in the format vk_{project}_{random}."""
    random_part = secrets.token_urlsafe(32)
    return f"vk_{project}_{random_part}"


def generate_webhook_secret() -> str:
    """Generate a webhook secret in the format whsec_xxxx."""
    random_part = secrets.token_urlsafe(32)
    return f"whsec_{random_part}"


class VoiceService:
    """Service for managing central voice service (port 8900)."""

    def __init__(self) -> None:
        """Initialize the voice service."""
        self.config = get_config()
        self.hostkit_db = get_db()

    def _voice_dir(self, project: str) -> Path:
        """Get the voice service directory for a project."""
        return Path(f"/home/{project}/.voice")

    def _voice_agents_dir(self, project: str) -> Path:
        """Get the agents directory for a project."""
        return self._voice_dir(project) / "agents"

    def voice_is_enabled(self, project: str) -> bool:
        """Check if voice service is enabled for a project."""
        with self.hostkit_db.connection() as conn:
            cur = conn.execute(
                "SELECT enabled FROM voice_projects WHERE project = ?",
                [project]
            )
            result = cur.fetchone()
            return result is not None and result["enabled"] == 1

    def enable_voice(self, project: str) -> dict[str, Any]:
        """Enable voice service for a project.

        Creates:
        - Entry in voice_projects table
        - Voice configuration directory /home/{project}/.voice/
        - Default agent template
        - API key and webhook secret

        Returns:
            Dictionary with voice_url, webhook_url, api_key, config_dir
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise VoiceServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_ALREADY_ENABLED",
                message=f"Voice service is already enabled for '{project}'",
                suggestion="Use 'hostkit voice disable' first to reset configuration",
            )

        # Generate API key and webhook secret
        api_key = generate_api_key(project)
        webhook_secret = generate_webhook_secret()

        # Create voice directory structure
        voice_dir = self._voice_dir(project)
        agents_dir = self._voice_agents_dir(project)

        voice_dir.mkdir(parents=True, exist_ok=True)
        agents_dir.mkdir(parents=True, exist_ok=True)

        # Create default agent template
        default_agent_path = agents_dir / "default.yaml"
        default_agent_content = """version: "1"
name: default
description: Default voice agent

persona:
  prompt: |
    You are a helpful AI assistant. Be concise and friendly.
    Keep responses under 3 sentences.

voice:
  provider: cartesia
  voice_id: professional-female
  speed: 1.0

constraints:
  max_duration: 10
"""
        default_agent_path.write_text(default_agent_content)

        # Insert into voice_projects table
        with self.hostkit_db.transaction() as conn:
            conn.execute(
                """INSERT INTO voice_projects (project, enabled, default_agent)
                   VALUES (?, 1, 'default')""",
                [project]
            )

        # Set environment variables
        from hostkit.services.env_service import EnvService
        env_service = EnvService()

        primary_domain = f"{project}.hostkit.dev"
        voice_url = f"https://{primary_domain}/voice"
        webhook_url = f"https://{primary_domain}/voice/webhook"

        env_service.set_env(project, "VOICE_API_KEY", api_key)
        env_service.set_env(project, "VOICE_API_URL", voice_url)
        env_service.set_env(project, "VOICE_WEBHOOK_SECRET", webhook_secret)

        # Set ownership
        import subprocess
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(voice_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        return {
            "voice_url": voice_url,
            "webhook_url": webhook_url,
            "api_key": api_key,
            "config_dir": str(voice_dir),
        }

    def disable_voice(self, project: str, force: bool = False) -> None:
        """Disable voice service for a project.

        Removes:
        - Entry from voice_projects table
        - Voice configuration directory

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise VoiceServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if voice is enabled
        if not self.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_NOT_ENABLED",
                message=f"Voice service is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise VoiceServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable voice service",
                suggestion=f"Add --force to confirm: 'hostkit voice disable {project} --force'",
            )

        # Delete from voice_projects table
        with self.hostkit_db.transaction() as conn:
            conn.execute("DELETE FROM voice_projects WHERE project = ?", [project])

        # Remove voice directory
        import shutil
        voice_dir = self._voice_dir(project)
        if voice_dir.exists():
            shutil.rmtree(voice_dir)

    def get_voice_status(self, project: str) -> dict[str, Any]:
        """Get voice service status for a project.

        Returns:
            Dictionary with voice configuration and statistics
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise VoiceServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        if not self.voice_is_enabled(project):
            return {
                "enabled": False,
                "project": project,
                "voice_url": None,
                "default_agent": None,
                "calls_today": 0,
                "active_calls": 0,
            }

        # Get default agent and call stats
        with self.hostkit_db.connection() as conn:
            cur = conn.execute(
                "SELECT default_agent FROM voice_projects WHERE project = ?",
                [project]
            )
            row = cur.fetchone()
            default_agent = row["default_agent"] if row else "default"

            # Get calls today
            cur = conn.execute(
                """SELECT COUNT(*) as count FROM voice_calls
                   WHERE project = ? AND DATE(started_at) = DATE('now')""",
                [project]
            )
            calls_today = cur.fetchone()["count"]

            # Get active calls
            cur = conn.execute(
                """SELECT COUNT(*) as count FROM voice_calls
                   WHERE project = ? AND ended_at IS NULL""",
                [project]
            )
            active_calls = cur.fetchone()["count"]

        primary_domain = f"{project}.hostkit.dev"
        voice_url = f"https://{primary_domain}/voice"

        return {
            "enabled": True,
            "project": project,
            "voice_url": voice_url,
            "default_agent": default_agent,
            "calls_today": calls_today,
            "active_calls": active_calls,
        }

    def create_agent(self, project: str, name: str) -> dict[str, Any]:
        """Create a new voice agent.

        Args:
            project: Project name
            name: Agent name

        Returns:
            Dictionary with agent details
        """
        if not self.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_NOT_ENABLED",
                message=f"Voice service is not enabled for '{project}'",
                suggestion=f"Enable voice first with 'hostkit voice enable {project}'",
            )

        # Validate agent name
        import re
        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            raise VoiceServiceError(
                code="INVALID_AGENT_NAME",
                message=f"Invalid agent name: {name}",
                suggestion="Use lowercase letters, numbers, and underscores only",
            )

        agents_dir = self._voice_agents_dir(project)
        agent_path = agents_dir / f"{name}.yaml"

        if agent_path.exists():
            raise VoiceServiceError(
                code="AGENT_EXISTS",
                message=f"Agent '{name}' already exists",
                suggestion="Use a different name or delete the existing agent",
            )

        # Create agent template
        agent_content = f"""version: "1"
name: {name}
description: Voice agent for {project}

persona:
  prompt: |
    You are a helpful AI assistant for {project}.
    Be concise, friendly, and professional.

voice:
  provider: cartesia
  voice_id: professional-female
  speed: 1.0

constraints:
  max_duration: 10
"""
        agent_path.write_text(agent_content)

        return {
            "name": name,
            "path": str(agent_path),
            "created": True,
        }

    def list_agents(self, project: str) -> list[dict[str, Any]]:
        """List all agents for a project."""
        if not self.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_NOT_ENABLED",
                message=f"Voice service is not enabled for '{project}'",
                suggestion=f"Enable voice first with 'hostkit voice enable {project}'",
            )

        agents_dir = self._voice_agents_dir(project)
        agents = []

        for agent_file in agents_dir.glob("*.yaml"):
            agents.append({
                "name": agent_file.stem,
                "description": f"Agent {agent_file.stem}",
            })

        return agents

    def initiate_call(
        self,
        project: str,
        agent: str,
        to: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Initiate an outbound voice call.

        Args:
            project: Project name
            agent: Agent name
            to: Recipient phone number (E.164 format)
            context: Optional call context

        Returns:
            Dictionary with call_id and status
        """
        if not self.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_NOT_ENABLED",
                message=f"Voice service is not enabled for '{project}'",
                suggestion=f"Enable voice first with 'hostkit voice enable {project}'",
            )

        # TODO: Implement actual call initiation via voice service API
        # For MVP, this is a placeholder
        import uuid
        call_id = f"call_{uuid.uuid4().hex[:16]}"

        # Insert call record
        with self.hostkit_db.transaction() as conn:
            conn.execute(
                """INSERT INTO voice_calls
                   (call_sid, project, agent_id, direction, to_number, started_at)
                   VALUES (?, ?, ?, 'outbound', ?, datetime('now'))""",
                [call_id, project, agent, to]
            )

        return {
            "call_id": call_id,
            "status": "queued",
            "agent": agent,
            "to": to,
        }
