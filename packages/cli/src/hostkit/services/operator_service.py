"""Operator management service for HostKit.

Operators are specialized users (typically AI agents) that can autonomously
manage VPS deployments with controlled sudo access to hostkit commands.
"""

import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db


# Default operator username
DEFAULT_OPERATOR_NAME = "ai-operator"


@dataclass
class Operator:
    """Operator user information."""
    username: str
    ssh_keys: list[str]
    created_at: str
    last_login: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Operator":
        """Create an Operator from a database dict."""
        # Parse ssh_keys from newline-separated string
        ssh_keys_str = data.get("ssh_keys") or ""
        ssh_keys = [k.strip() for k in ssh_keys_str.split("\n") if k.strip()]

        return cls(
            username=data["username"],
            ssh_keys=ssh_keys,
            created_at=data["created_at"],
            last_login=data.get("last_login"),
        )


class OperatorServiceError(Exception):
    """Base exception for operator service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class OperatorService:
    """Service for managing HostKit operators."""

    def __init__(self) -> None:
        self.db = get_db()
        self.config = get_config()

    def setup(self, username: str | None = None) -> Operator:
        """Create and configure an operator user.

        Args:
            username: Operator username (defaults to 'ai-operator')

        Returns:
            The created Operator

        Raises:
            OperatorServiceError: If setup fails
        """
        username = username or DEFAULT_OPERATOR_NAME

        # Check if operator already exists in database
        existing = self.db.get_operator(username)
        if existing:
            raise OperatorServiceError(
                code="OPERATOR_EXISTS",
                message=f"Operator '{username}' already exists",
                suggestion="Use 'hostkit operator add-key' to add SSH keys or 'hostkit operator revoke' to remove",
            )

        # Check if Linux user already exists
        try:
            pwd.getpwnam(username)
            raise OperatorServiceError(
                code="USER_EXISTS",
                message=f"Linux user '{username}' already exists but is not registered as an operator",
                suggestion="Choose a different username or manually remove the existing user",
            )
        except KeyError:
            pass  # User doesn't exist, which is what we want

        try:
            # 1. Create Linux user
            self._create_linux_user(username)

            # 2. Create .ssh directory
            self._create_ssh_directory(username)

            # 3. Create sudoers rules
            self._create_sudoers(username)

            # 4. Register in database
            operator_data = self.db.create_operator(username)

            return Operator.from_dict(operator_data)

        except OperatorServiceError:
            raise
        except Exception as e:
            # Clean up on failure
            self._cleanup_failed_operator(username)
            raise OperatorServiceError(
                code="OPERATOR_SETUP_FAILED",
                message=f"Failed to create operator: {e}",
                suggestion="Check system logs for details",
            )

    def add_key(
        self,
        username: str | None = None,
        key: str | None = None,
        github_user: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Add an SSH key to an operator.

        Args:
            username: Operator username (defaults to 'ai-operator')
            key: SSH public key string
            github_user: GitHub username to fetch keys from
            file_path: Path to file containing SSH public key

        Returns:
            Dict with added keys info

        Raises:
            OperatorServiceError: If adding key fails
        """
        username = username or DEFAULT_OPERATOR_NAME

        # Verify operator exists
        operator = self.db.get_operator(username)
        if not operator:
            raise OperatorServiceError(
                code="OPERATOR_NOT_FOUND",
                message=f"Operator '{username}' does not exist",
                suggestion="Run 'hostkit operator setup' first",
            )

        # Get keys to add
        keys_to_add: list[str] = []

        if key:
            # Validate key format
            self._validate_ssh_key(key)
            keys_to_add.append(key.strip())

        if github_user:
            # Fetch keys from GitHub
            github_keys = self._fetch_github_keys(github_user)
            keys_to_add.extend(github_keys)

        if file_path:
            # Read key from file
            path = Path(file_path)
            if not path.exists():
                raise OperatorServiceError(
                    code="FILE_NOT_FOUND",
                    message=f"Key file not found: {file_path}",
                )
            file_key = path.read_text().strip()
            self._validate_ssh_key(file_key)
            keys_to_add.append(file_key)

        if not keys_to_add:
            raise OperatorServiceError(
                code="NO_KEY_PROVIDED",
                message="No SSH key provided",
                suggestion="Provide a key via --key, --github, or --file",
            )

        # Get existing keys
        existing_keys_str = operator.get("ssh_keys") or ""
        existing_keys = [k.strip() for k in existing_keys_str.split("\n") if k.strip()]

        # Add new keys (avoid duplicates)
        added_count = 0
        for new_key in keys_to_add:
            if new_key not in existing_keys:
                existing_keys.append(new_key)
                added_count += 1

        # Update database
        new_keys_str = "\n".join(existing_keys)
        self.db.update_operator(username, ssh_keys=new_keys_str)

        # Update authorized_keys file
        self._update_authorized_keys(username, existing_keys)

        return {
            "username": username,
            "keys_added": added_count,
            "total_keys": len(existing_keys),
        }

    def test_access(self, username: str | None = None) -> dict[str, Any]:
        """Test operator access configuration.

        Args:
            username: Operator username (defaults to 'ai-operator')

        Returns:
            Dict with test results

        Raises:
            OperatorServiceError: If operator doesn't exist
        """
        username = username or DEFAULT_OPERATOR_NAME

        # Verify operator exists
        operator = self.db.get_operator(username)
        if not operator:
            raise OperatorServiceError(
                code="OPERATOR_NOT_FOUND",
                message=f"Operator '{username}' does not exist",
                suggestion="Run 'hostkit operator setup' first",
            )

        results: dict[str, Any] = {
            "username": username,
            "checks": {},
            "overall": True,
        }

        # Check Linux user exists
        try:
            pwd.getpwnam(username)
            results["checks"]["linux_user"] = {"status": "pass", "message": "User exists"}
        except KeyError:
            results["checks"]["linux_user"] = {"status": "fail", "message": "User does not exist"}
            results["overall"] = False

        # Check .ssh directory exists
        ssh_dir = Path(f"/home/{username}/.ssh")
        if ssh_dir.exists():
            results["checks"]["ssh_directory"] = {"status": "pass", "message": "Directory exists"}
        else:
            results["checks"]["ssh_directory"] = {"status": "fail", "message": "Directory missing"}
            results["overall"] = False

        # Check authorized_keys exists and has keys
        auth_keys = ssh_dir / "authorized_keys"
        if auth_keys.exists():
            key_count = len([line for line in auth_keys.read_text().split("\n") if line.strip()])
            if key_count > 0:
                results["checks"]["authorized_keys"] = {
                    "status": "pass",
                    "message": f"{key_count} key(s) configured",
                }
            else:
                results["checks"]["authorized_keys"] = {
                    "status": "warn",
                    "message": "File exists but no keys configured",
                }
        else:
            results["checks"]["authorized_keys"] = {
                "status": "warn",
                "message": "No authorized_keys file (no SSH access yet)",
            }

        # Check sudoers file exists
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-operator-{username}")
        if sudoers_path.exists():
            results["checks"]["sudoers"] = {"status": "pass", "message": "Sudoers rules configured"}
        else:
            results["checks"]["sudoers"] = {"status": "fail", "message": "Sudoers rules missing"}
            results["overall"] = False

        # Test sudo access (only if we're root and user exists)
        try:
            result = subprocess.run(
                ["sudo", "-u", username, "sudo", "-n", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "/usr/local/bin/hostkit" in result.stdout:
                results["checks"]["sudo_access"] = {
                    "status": "pass",
                    "message": "Sudo access to hostkit verified",
                }
            else:
                results["checks"]["sudo_access"] = {
                    "status": "warn",
                    "message": "Sudo access may not be configured correctly",
                }
        except (subprocess.SubprocessError, FileNotFoundError):
            results["checks"]["sudo_access"] = {
                "status": "skip",
                "message": "Could not verify sudo access",
            }

        return results

    def revoke(self, username: str | None = None) -> dict[str, Any]:
        """Revoke an operator's access and remove the user.

        Args:
            username: Operator username (defaults to 'ai-operator')

        Returns:
            Dict with revocation info

        Raises:
            OperatorServiceError: If revocation fails
        """
        username = username or DEFAULT_OPERATOR_NAME

        # Verify operator exists
        operator = self.db.get_operator(username)
        if not operator:
            raise OperatorServiceError(
                code="OPERATOR_NOT_FOUND",
                message=f"Operator '{username}' does not exist",
                suggestion="Run 'hostkit operator list' to see registered operators",
            )

        # 1. Remove sudoers rules
        self._remove_sudoers(username)

        # 2. Delete Linux user and home directory
        self._delete_linux_user(username)

        # 3. Remove from database
        self.db.delete_operator(username)

        return {
            "username": username,
            "revoked": True,
            "message": f"Operator '{username}' has been revoked and removed",
        }

    def list_operators(self) -> list[Operator]:
        """List all registered operators.

        Returns:
            List of Operator objects
        """
        operators = self.db.list_operators()
        return [Operator.from_dict(op) for op in operators]

    def get_operator(self, username: str) -> Operator | None:
        """Get an operator by username.

        Args:
            username: Operator username

        Returns:
            Operator or None if not found
        """
        operator = self.db.get_operator(username)
        if operator:
            return Operator.from_dict(operator)
        return None

    # Private helper methods

    def _create_linux_user(self, username: str) -> None:
        """Create a Linux user for the operator."""
        subprocess.run(
            [
                "useradd",
                "--create-home",
                "--home-dir", f"/home/{username}",
                "--shell", "/bin/bash",
                "--comment", "HostKit Operator",
                username,
            ],
            check=True,
            capture_output=True,
        )

    def _delete_linux_user(self, username: str) -> None:
        """Delete a Linux user and their home directory."""
        try:
            subprocess.run(
                ["userdel", "--remove", username],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # User might not exist, that's okay
            pass

    def _create_ssh_directory(self, username: str) -> None:
        """Create the .ssh directory for an operator."""
        ssh_dir = Path(f"/home/{username}/.ssh")
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Create empty authorized_keys file
        auth_keys = ssh_dir / "authorized_keys"
        auth_keys.touch(mode=0o600)

        # Set ownership
        subprocess.run(
            ["chown", "-R", f"{username}:{username}", str(ssh_dir)],
            check=True,
            capture_output=True,
        )

    def _create_sudoers(self, username: str) -> None:
        """Create sudoers rules for an operator."""
        # Load template
        template_path = self.config.templates_dir / "operator-sudoers.j2"

        if template_path.exists():
            template_content = template_path.read_text()
            template = Template(template_content)
            sudoers_content = template.render(operator_name=username)
        else:
            # Fallback inline template
            sudoers_content = f"""# HostKit operator: {username}
# Generated by HostKit - DO NOT EDIT MANUALLY
{username} ALL=(root) NOPASSWD: /usr/local/bin/hostkit *
"""

        # Write to sudoers.d directory
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-operator-{username}")
        sudoers_path.write_text(sudoers_content)

        # Set correct permissions (must be 0440 or 0400)
        subprocess.run(["chmod", "0440", str(sudoers_path)], check=True)

        # Validate the sudoers file
        result = subprocess.run(
            ["visudo", "-c", "-f", str(sudoers_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Invalid sudoers file - remove it and raise error
            sudoers_path.unlink()
            raise OperatorServiceError(
                code="INVALID_SUDOERS",
                message="Generated sudoers file failed validation",
                suggestion="Check the operator-sudoers.j2 template",
            )

    def _remove_sudoers(self, username: str) -> None:
        """Remove sudoers rules for an operator."""
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-operator-{username}")
        if sudoers_path.exists():
            sudoers_path.unlink()

    def _validate_ssh_key(self, key: str) -> None:
        """Validate an SSH public key format."""
        key = key.strip()
        # Basic validation - check it starts with a valid key type
        valid_prefixes = (
            "ssh-rsa",
            "ssh-ed25519",
            "ssh-dss",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "sk-ssh-ed25519@openssh.com",
            "sk-ecdsa-sha2-nistp256@openssh.com",
        )
        if not any(key.startswith(prefix) for prefix in valid_prefixes):
            raise OperatorServiceError(
                code="INVALID_SSH_KEY",
                message="Invalid SSH public key format",
                suggestion="Provide a valid SSH public key (e.g., ssh-ed25519 AAAA... comment)",
            )

    def _fetch_github_keys(self, github_user: str) -> list[str]:
        """Fetch SSH public keys from GitHub."""
        url = f"https://github.com/{github_user}.keys"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            keys = [k.strip() for k in response.text.split("\n") if k.strip()]
            if not keys:
                raise OperatorServiceError(
                    code="NO_GITHUB_KEYS",
                    message=f"No SSH keys found for GitHub user '{github_user}'",
                    suggestion="Make sure the user has public SSH keys on GitHub",
                )
            return keys
        except requests.RequestException as e:
            raise OperatorServiceError(
                code="GITHUB_FETCH_FAILED",
                message=f"Failed to fetch keys from GitHub: {e}",
                suggestion="Check your network connection and the GitHub username",
            )

    def _update_authorized_keys(self, username: str, keys: list[str]) -> None:
        """Update the authorized_keys file for an operator."""
        auth_keys_path = Path(f"/home/{username}/.ssh/authorized_keys")
        auth_keys_path.write_text("\n".join(keys) + "\n" if keys else "")
        auth_keys_path.chmod(0o600)

        # Set ownership
        subprocess.run(
            ["chown", f"{username}:{username}", str(auth_keys_path)],
            check=True,
            capture_output=True,
        )

    def _cleanup_failed_operator(self, username: str) -> None:
        """Clean up resources after a failed operator setup."""
        try:
            self._remove_sudoers(username)
            self._delete_linux_user(username)
            self.db.delete_operator(username)
        except Exception:
            pass  # Best effort cleanup
