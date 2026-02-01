"""Agent configuration loader."""
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class AgentLoader:
    """Loads agent YAML configs from project directories."""

    def __init__(self, projects_base: str = "/home"):
        self.projects_base = Path(projects_base)

    def load_agent(self, project: str, agent_name: str) -> Optional[Dict[str, Any]]:
        """Load agent config from /home/{project}/.voice/agents/{name}.yaml"""
        agent_path = self.projects_base / project / ".voice" / "agents" / f"{agent_name}.yaml"

        if not agent_path.exists():
            return None

        try:
            with open(agent_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Error loading agent {project}/{agent_name}: {e}")
            return None

    def get_default_config(self) -> Dict[str, Any]:
        """Fallback agent config."""
        return {
            "name": "default",
            "llm_provider": "openai",
            "llm_model": "gpt-4",
            "system_prompt": "You are a helpful voice assistant.",
            "tts_provider": "cartesia",
            "tts_voice": "default",
            "stt_provider": "deepgram"
        }
