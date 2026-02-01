"""SSH management service for HostKit."""

import os
import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from hostkit.database import get_db

# Maximum number of SSH keys per project
MAX_KEYS_PER_PROJECT = 20


@dataclass
class SSHKey:
    """Represents an SSH public key."""

    fingerprint: str
    key_type: str
    comment: str
    raw_key: str


@dataclass
class SSHSession:
    """Represents an active SSH session."""

    user: str
    tty: str
    login_time: str
    from_host: str | None


def get_authorized_keys_path(project: str) -> Path:
    """Get the authorized_keys file path for a project."""
    return Path(f"/home/{project}/.ssh/authorized_keys")


def ensure_ssh_dir(project: str) -> None:
    """Ensure .ssh directory exists with correct permissions."""
    ssh_dir = Path(f"/home/{project}/.ssh")
    if not ssh_dir.exists():
        ssh_dir.mkdir(mode=0o700, parents=True)
        try:
            pw = pwd.getpwnam(project)
            os.chown(ssh_dir, pw.pw_uid, pw.pw_gid)
        except KeyError:
            pass


def get_key_fingerprint(public_key: str) -> tuple[str, str, str]:
    """Get SHA256 fingerprint, type, and comment from a public key.

    Returns:
        Tuple of (fingerprint, key_type, comment)

    Raises:
        ValueError: If the key is invalid
    """
    result = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=public_key.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Invalid SSH key: {result.stderr.decode()}")

    parts = result.stdout.decode().strip().split()
    fingerprint = parts[1]
    key_type = parts[-1].strip("()")
    comment = " ".join(parts[2:-1]) if len(parts) > 3 else ""

    return fingerprint, key_type, comment


def validate_key(public_key: str) -> bool:
    """Validate that a string is a valid SSH public key."""
    try:
        get_key_fingerprint(public_key)
        return True
    except ValueError:
        return False


def fetch_github_keys(username: str) -> list[str]:
    """Fetch public keys from a GitHub username.

    Args:
        username: GitHub username

    Returns:
        List of public key strings

    Raises:
        ValueError: If user not found or has no keys
    """
    url = f"https://github.com/{username}.keys"
    resp = requests.get(url, timeout=10)

    if resp.status_code == 404:
        raise ValueError(f"GitHub user '{username}' not found")

    resp.raise_for_status()

    keys = [k.strip() for k in resp.text.strip().split("\n") if k.strip()]
    if not keys:
        raise ValueError(f"GitHub user '{username}' has no public keys")

    return keys


def list_keys(project: str) -> list[SSHKey]:
    """List all authorized SSH keys for a project."""
    auth_keys_path = get_authorized_keys_path(project)

    if not auth_keys_path.exists():
        return []

    keys = []
    for line in auth_keys_path.read_text().strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            fingerprint, key_type, comment = get_key_fingerprint(line)
            keys.append(
                SSHKey(
                    fingerprint=fingerprint,
                    key_type=key_type,
                    comment=comment,
                    raw_key=line,
                )
            )
        except ValueError:
            continue

    return keys


def add_key(project: str, public_key: str) -> SSHKey:
    """Add an SSH key to a project's authorized_keys.

    Args:
        project: Project name
        public_key: SSH public key string

    Returns:
        SSHKey object for the added key

    Raises:
        ValueError: If key is invalid, already exists, or key limit reached
    """
    fingerprint, key_type, comment = get_key_fingerprint(public_key)

    existing_keys = list_keys(project)

    # Check key limit
    if len(existing_keys) >= MAX_KEYS_PER_PROJECT:
        raise ValueError(
            f"Maximum SSH keys ({MAX_KEYS_PER_PROJECT}) reached for project '{project}'. "
            "Remove unused keys before adding new ones."
        )

    for existing in existing_keys:
        if existing.fingerprint == fingerprint:
            raise ValueError(f"Key already exists: {fingerprint}")

    ensure_ssh_dir(project)

    auth_keys_path = get_authorized_keys_path(project)

    if not auth_keys_path.exists():
        auth_keys_path.touch(mode=0o600)
        try:
            pw = pwd.getpwnam(project)
            os.chown(auth_keys_path, pw.pw_uid, pw.pw_gid)
        except KeyError:
            pass

    with open(auth_keys_path, "a") as f:
        f.write(f"{public_key.strip()}\n")

    # Record in audit log
    db = get_db()
    db.record_ssh_key_action(
        project=project,
        action="add",
        fingerprint=fingerprint,
        key_comment=comment if comment else None,
    )

    return SSHKey(
        fingerprint=fingerprint,
        key_type=key_type,
        comment=comment,
        raw_key=public_key,
    )


def remove_key(project: str, fingerprint: str) -> bool:
    """Remove an SSH key by fingerprint.

    Args:
        project: Project name
        fingerprint: Key fingerprint (with or without SHA256: prefix)

    Returns:
        True if key was removed

    Raises:
        ValueError: If key not found or no authorized_keys file
    """
    if not fingerprint.startswith("SHA256:"):
        fingerprint = f"SHA256:{fingerprint}"

    auth_keys_path = get_authorized_keys_path(project)

    if not auth_keys_path.exists():
        raise ValueError(f"No authorized_keys file for project '{project}'")

    lines = auth_keys_path.read_text().strip().split("\n")
    new_lines = []
    removed = False
    removed_comment = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            key_fp, _, comment = get_key_fingerprint(line)
            if key_fp == fingerprint:
                removed = True
                removed_comment = comment
                continue
        except ValueError:
            pass

        new_lines.append(line)

    if not removed:
        raise ValueError(f"Key not found: {fingerprint}")

    auth_keys_path.write_text("\n".join(new_lines) + "\n" if new_lines else "")

    # Record in audit log
    db = get_db()
    db.record_ssh_key_action(
        project=project,
        action="remove",
        fingerprint=fingerprint,
        key_comment=removed_comment,
    )

    return True


def get_sessions(project: str) -> list[SSHSession]:
    """Get active SSH sessions for a project user."""
    result = subprocess.run(["who"], capture_output=True, text=True)
    sessions = []

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if parts and parts[0] == project:
            sessions.append(
                SSHSession(
                    user=parts[0],
                    tty=parts[1],
                    login_time=" ".join(parts[2:4]) if len(parts) >= 4 else parts[2],
                    from_host=parts[4].strip("()") if len(parts) > 4 else None,
                )
            )

    return sessions


def kick_session(tty: str) -> bool:
    """Kill an SSH session by TTY.

    Args:
        tty: TTY name (e.g., "pts/0")

    Returns:
        True if session was killed
    """
    result = subprocess.run(
        ["pkill", "-9", "-t", tty],
        capture_output=True,
    )
    return result.returncode == 0


def kick_all_sessions(project: str) -> int:
    """Kill all SSH sessions for a project user.

    Returns:
        Number of sessions kicked
    """
    sessions = get_sessions(project)
    count = 0
    for session in sessions:
        if kick_session(session.tty):
            count += 1
    return count


# --- SSH Enable/Disable Functionality ---


def enable_ssh(project: str, changed_by: str | None = None) -> bool:
    """Enable SSH access for a project.

    Args:
        project: Project name
        changed_by: Username making the change (for audit)

    Returns:
        True if SSH was previously disabled

    Raises:
        ValueError: If project user doesn't exist
        RuntimeError: If shell change fails
    """
    try:
        pwd.getpwnam(project)
    except KeyError:
        raise ValueError(f"Project user '{project}' does not exist")

    db = get_db()
    was_disabled = not db.get_ssh_enabled(project)

    result = subprocess.run(
        ["usermod", "-s", "/bin/bash", project],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to change shell: {result.stderr.decode()}")

    db.set_ssh_enabled(project, True, changed_by)

    return was_disabled


def disable_ssh(project: str, force: bool = False, changed_by: str | None = None) -> int:
    """Disable SSH access for a project.

    Args:
        project: Project name
        force: If True, kick all active sessions first
        changed_by: Username making the change (for audit)

    Returns:
        Number of sessions kicked

    Raises:
        ValueError: If project user doesn't exist or has active sessions without force
        RuntimeError: If shell change fails
    """
    try:
        pwd.getpwnam(project)
    except KeyError:
        raise ValueError(f"Project user '{project}' does not exist")

    sessions = get_sessions(project)
    if sessions and not force:
        raise ValueError(
            f"Project '{project}' has {len(sessions)} active session(s). "
            "Use --force to kick all sessions and disable SSH."
        )

    kicked = 0
    if force and sessions:
        kicked = kick_all_sessions(project)

    result = subprocess.run(
        ["usermod", "-s", "/sbin/nologin", project],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to change shell: {result.stderr.decode()}")

    db = get_db()
    db.set_ssh_enabled(project, False, changed_by)

    return kicked


def get_ssh_status(project: str) -> dict:
    """Get SSH status for a single project.

    Args:
        project: Project name

    Returns:
        Dict with project, enabled, shell, active_sessions, authorized_keys

    Raises:
        ValueError: If project user doesn't exist
    """
    try:
        pw = pwd.getpwnam(project)
        shell = pw.pw_shell
    except KeyError:
        raise ValueError(f"Project user '{project}' does not exist")

    db = get_db()
    enabled = db.get_ssh_enabled(project)
    sessions = get_sessions(project)
    keys = list_keys(project)

    return {
        "project": project,
        "enabled": enabled,
        "shell": shell,
        "active_sessions": len(sessions),
        "authorized_keys": len(keys),
    }


def get_all_projects_ssh_status() -> list[dict]:
    """Get SSH status for all project users.

    Returns:
        List of status dicts for each project user
    """
    statuses = []

    for pw in pwd.getpwall():
        # Project users have UID >= 1000, home in /home/, and aren't 'ubuntu'
        if pw.pw_uid >= 1000 and pw.pw_dir.startswith("/home/") and pw.pw_name != "ubuntu":
            try:
                status = get_ssh_status(pw.pw_name)
                statuses.append(status)
            except ValueError:
                continue

    return statuses
