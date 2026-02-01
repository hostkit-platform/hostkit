"""Mail server management for HostKit using Postfix and Dovecot."""

import os
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hostkit.config import get_config
from hostkit.registry import CapabilitiesRegistry, ServiceMeta


# Register mail service capabilities
CapabilitiesRegistry.register_service(ServiceMeta(
    name="mail",
    description="Email sending via Postfix",
    provision_flag=None,
    enable_command="hostkit mail enable {project}",
    env_vars_provided=["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"],
    related_commands=["mail enable", "mail disable", "mail add"],
))


@dataclass
class MailDomain:
    """Information about a configured mail domain."""

    name: str
    dkim_enabled: bool
    dkim_selector: str
    created_at: str
    mailboxes: list[str] = field(default_factory=list)


@dataclass
class Mailbox:
    """Information about a virtual mailbox."""

    address: str
    domain: str
    local_part: str
    project: str
    maildir: str
    created_at: str


@dataclass
class QueueEntry:
    """Information about a mail queue entry."""

    queue_id: str
    size: str
    arrival_time: str
    sender: str
    recipients: list[str]
    status: str


class MailError(Exception):
    """Base exception for mail errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class MailService:
    """Service for managing mail server with Postfix and Dovecot."""

    # Configuration paths
    POSTFIX_MAIN_CF = Path("/etc/postfix/main.cf")
    POSTFIX_MASTER_CF = Path("/etc/postfix/master.cf")
    VIRTUAL_DOMAINS_FILE = Path("/etc/postfix/virtual_mailbox_domains")
    VIRTUAL_MAILBOX_FILE = Path("/etc/postfix/virtual_mailbox_maps")
    DOVECOT_USERS_FILE = Path("/etc/dovecot/users")
    MAILDIR_BASE = Path("/var/mail/vhosts")
    DKIM_KEYS_DIR = Path("/etc/opendkim/keys")
    DKIM_KEY_TABLE = Path("/etc/opendkim/key.table")
    DKIM_SIGNING_TABLE = Path("/etc/opendkim/signing.table")
    MAIL_CONFIG_PATH = Path("/etc/hostkit/mail.yaml")

    # Virtual mail user/group IDs (created during setup)
    VMAIL_UID = 5000
    VMAIL_GID = 5000

    def __init__(self) -> None:
        self.config = get_config()
        self._mail_config: dict[str, Any] | None = None

    # ─────────────────────────────────────────────────────────────────────────
    # Configuration Management
    # ─────────────────────────────────────────────────────────────────────────

    def _load_mail_config(self) -> dict[str, Any]:
        """Load mail configuration from disk."""
        if self._mail_config is not None:
            return self._mail_config

        if not self.MAIL_CONFIG_PATH.exists():
            # Return default/empty config
            self._mail_config = {"domains": {}, "mailboxes": {}}
            return self._mail_config

        try:
            with open(self.MAIL_CONFIG_PATH) as f:
                self._mail_config = yaml.safe_load(f) or {"domains": {}, "mailboxes": {}}
        except yaml.YAMLError as e:
            raise MailError(
                code="CONFIG_INVALID",
                message=f"Invalid mail configuration: {e}",
            )

        return self._mail_config

    def _save_mail_config(self, config: dict[str, Any]) -> None:
        """Save mail configuration to disk."""
        self.MAIL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(self.MAIL_CONFIG_PATH, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False)
        self.MAIL_CONFIG_PATH.chmod(0o600)
        self._mail_config = config

    def is_installed(self) -> bool:
        """Check if mail server (Postfix) is installed."""
        try:
            result = subprocess.run(
                ["which", "postfix"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_service_status(self) -> dict[str, Any]:
        """Get status of mail services (Postfix, Dovecot, OpenDKIM)."""
        services = {}

        for service_name in ["postfix", "dovecot", "opendkim"]:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", service_name],
                    capture_output=True,
                    text=True,
                )
                status = result.stdout.strip()
                services[service_name] = {
                    "running": status == "active",
                    "status": status,
                }
            except Exception:
                services[service_name] = {
                    "running": False,
                    "status": "unknown",
                }

        return services

    # ─────────────────────────────────────────────────────────────────────────
    # Installation and Setup
    # ─────────────────────────────────────────────────────────────────────────

    def setup_mail_server(self, hostname: str) -> dict[str, Any]:
        """Initial setup of mail server components.

        This installs and configures Postfix, Dovecot, and OpenDKIM.
        Should be run once during initial VPS setup.
        """
        results = {
            "postfix": False,
            "dovecot": False,
            "opendkim": False,
            "vmail_user": False,
        }

        # Create vmail user/group for virtual mailboxes
        try:
            # Check if vmail group exists
            result = subprocess.run(
                ["getent", "group", "vmail"],
                capture_output=True,
            )
            if result.returncode != 0:
                subprocess.run(
                    ["groupadd", "-g", str(self.VMAIL_GID), "vmail"],
                    check=True,
                )

            # Check if vmail user exists
            result = subprocess.run(
                ["getent", "passwd", "vmail"],
                capture_output=True,
            )
            if result.returncode != 0:
                subprocess.run(
                    [
                        "useradd",
                        "-u", str(self.VMAIL_UID),
                        "-g", "vmail",
                        "-d", str(self.MAILDIR_BASE),
                        "-s", "/usr/sbin/nologin",
                        "vmail",
                    ],
                    check=True,
                )

            # Create maildir base
            self.MAILDIR_BASE.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["chown", f"{self.VMAIL_UID}:{self.VMAIL_GID}", str(self.MAILDIR_BASE)],
                check=True,
            )
            results["vmail_user"] = True
        except subprocess.CalledProcessError as e:
            raise MailError(
                code="VMAIL_SETUP_FAILED",
                message=f"Failed to create vmail user: {e}",
            )

        # Install packages if needed
        packages_to_install = []
        for pkg in ["postfix", "dovecot-imapd", "dovecot-lmtpd", "opendkim", "opendkim-tools"]:
            result = subprocess.run(
                ["dpkg", "-l", pkg],
                capture_output=True,
            )
            if result.returncode != 0:
                packages_to_install.append(pkg)

        if packages_to_install:
            try:
                subprocess.run(
                    ["apt-get", "update"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["apt-get", "install", "-y"] + packages_to_install,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise MailError(
                    code="INSTALL_FAILED",
                    message=f"Failed to install mail packages: {e}",
                    suggestion="Check apt sources and network connectivity",
                )

        # Configure Postfix
        self._configure_postfix(hostname)
        results["postfix"] = True

        # Configure Dovecot
        self._configure_dovecot()
        results["dovecot"] = True

        # Configure OpenDKIM
        self._configure_opendkim()
        results["opendkim"] = True

        # Initialize empty virtual files
        self._init_virtual_files()

        # Configure submission port (587) in master.cf
        self._configure_submission_port()

        # Restart services
        for svc in ["postfix", "dovecot", "opendkim"]:
            subprocess.run(["systemctl", "restart", svc], check=False)
            subprocess.run(["systemctl", "enable", svc], check=False)

        # Open firewall ports for mail
        firewall_result = self._configure_firewall()
        results["firewall"] = firewall_result

        return {
            "setup_complete": True,
            "services": results,
            "hostname": hostname,
            "message": "Mail server setup complete",
        }

    def _configure_postfix(self, hostname: str) -> None:
        """Configure Postfix for virtual domains."""
        # Backup existing config
        if self.POSTFIX_MAIN_CF.exists():
            backup = self.POSTFIX_MAIN_CF.with_suffix(".cf.bak")
            if not backup.exists():
                import shutil
                shutil.copy(self.POSTFIX_MAIN_CF, backup)

        postfix_config = f"""# Postfix configuration for HostKit
# Generated by hostkit mail setup

# Basic settings
myhostname = {hostname}
mydomain = {hostname.split('.', 1)[1] if '.' in hostname else hostname}
myorigin = $mydomain
mydestination = localhost
mynetworks = 127.0.0.0/8 [::ffff:127.0.0.0]/104 [::1]/128

# Virtual mailbox settings
virtual_mailbox_domains = hash:{self.VIRTUAL_DOMAINS_FILE}
virtual_mailbox_base = {self.MAILDIR_BASE}
virtual_mailbox_maps = hash:{self.VIRTUAL_MAILBOX_FILE}
virtual_minimum_uid = 100
virtual_uid_maps = static:{self.VMAIL_UID}
virtual_gid_maps = static:{self.VMAIL_GID}

# Transport to Dovecot via LMTP
virtual_transport = lmtp:unix:private/dovecot-lmtp

# TLS settings
smtpd_tls_cert_file = /etc/letsencrypt/live/{hostname}/fullchain.pem
smtpd_tls_key_file = /etc/letsencrypt/live/{hostname}/privkey.pem
smtpd_tls_security_level = may
smtp_tls_security_level = may
smtpd_tls_auth_only = yes

# SASL authentication via Dovecot
smtpd_sasl_type = dovecot
smtpd_sasl_path = private/auth
smtpd_sasl_auth_enable = yes
smtpd_sasl_security_options = noanonymous
smtpd_sasl_local_domain = $myhostname

# Recipient restrictions
smtpd_recipient_restrictions =
    permit_sasl_authenticated,
    permit_mynetworks,
    reject_unauth_destination

# Sender restrictions
smtpd_sender_restrictions =
    permit_sasl_authenticated,
    permit_mynetworks,
    reject_non_fqdn_sender,
    reject_unknown_sender_domain

# DKIM milter
smtpd_milters = inet:localhost:8891
non_smtpd_milters = inet:localhost:8891
milter_default_action = accept
milter_protocol = 6

# Message size limit (25MB)
message_size_limit = 26214400

# Mailbox size limit (1GB per mailbox)
virtual_mailbox_limit = 1073741824
"""

        with open(self.POSTFIX_MAIN_CF, "w") as f:
            f.write(postfix_config)

    def _configure_dovecot(self) -> None:
        """Configure Dovecot for virtual mailboxes."""
        dovecot_conf_dir = Path("/etc/dovecot/conf.d")

        # 10-mail.conf - mailbox locations
        mail_conf = f"""# Dovecot mail configuration for HostKit
mail_location = maildir:{self.MAILDIR_BASE}/%d/%n
mail_privileged_group = vmail
namespace inbox {{
    inbox = yes
    separator = /
}}
"""
        with open(dovecot_conf_dir / "10-mail.conf", "w") as f:
            f.write(mail_conf)

        # 10-auth.conf - authentication
        auth_conf = """# Dovecot auth configuration for HostKit
disable_plaintext_auth = yes
auth_mechanisms = plain login

passdb {
    driver = passwd-file
    args = scheme=SHA512-CRYPT username_format=%u /etc/dovecot/users
}

userdb {
    driver = static
    args = uid=5000 gid=5000 home=/var/mail/vhosts/%d/%n
}
"""
        with open(dovecot_conf_dir / "10-auth.conf", "w") as f:
            f.write(auth_conf)

        # 10-master.conf - service sockets
        master_conf = """# Dovecot master configuration for HostKit
service lmtp {
    unix_listener /var/spool/postfix/private/dovecot-lmtp {
        mode = 0600
        user = postfix
        group = postfix
    }
}

service auth {
    unix_listener /var/spool/postfix/private/auth {
        mode = 0660
        user = postfix
        group = postfix
    }
}

service imap-login {
    inet_listener imap {
        port = 143
    }
    inet_listener imaps {
        port = 993
        ssl = yes
    }
}
"""
        with open(dovecot_conf_dir / "10-master.conf", "w") as f:
            f.write(master_conf)

        # 10-ssl.conf - TLS settings
        ssl_conf = """# Dovecot SSL configuration for HostKit
ssl = required
ssl_cert = </etc/letsencrypt/live/*/fullchain.pem
ssl_key = </etc/letsencrypt/live/*/privkey.pem
ssl_min_protocol = TLSv1.2
"""
        with open(dovecot_conf_dir / "10-ssl.conf", "w") as f:
            f.write(ssl_conf)

        # Create empty users file
        self.DOVECOT_USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not self.DOVECOT_USERS_FILE.exists():
            self.DOVECOT_USERS_FILE.touch()
            self.DOVECOT_USERS_FILE.chmod(0o600)

    def _configure_opendkim(self) -> None:
        """Configure OpenDKIM for signing outgoing mail."""
        opendkim_conf = Path("/etc/opendkim.conf")

        config = f"""# OpenDKIM configuration for HostKit
Syslog yes
UMask 007
Mode sv
Socket inet:8891@localhost
PidFile /run/opendkim/opendkim.pid

Canonicalization relaxed/simple
SignatureAlgorithm rsa-sha256

KeyTable {self.DKIM_KEY_TABLE}
SigningTable refile:{self.DKIM_SIGNING_TABLE}

ExternalIgnoreList refile:/etc/opendkim/trusted.hosts
InternalHosts refile:/etc/opendkim/trusted.hosts
"""
        with open(opendkim_conf, "w") as f:
            f.write(config)

        # Create directories and empty files
        self.DKIM_KEYS_DIR.mkdir(parents=True, exist_ok=True)
        self.DKIM_KEY_TABLE.parent.mkdir(parents=True, exist_ok=True)

        for path in [self.DKIM_KEY_TABLE, self.DKIM_SIGNING_TABLE]:
            if not path.exists():
                path.touch()

        # Create trusted hosts file
        trusted_hosts = Path("/etc/opendkim/trusted.hosts")
        if not trusted_hosts.exists():
            with open(trusted_hosts, "w") as f:
                f.write("127.0.0.1\nlocalhost\n")

    def _init_virtual_files(self) -> None:
        """Initialize empty virtual mailbox files."""
        for path in [self.VIRTUAL_DOMAINS_FILE, self.VIRTUAL_MAILBOX_FILE]:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()

        # Run postmap to create db files
        for path in [self.VIRTUAL_DOMAINS_FILE, self.VIRTUAL_MAILBOX_FILE]:
            subprocess.run(["postmap", str(path)], check=False)

    def _configure_submission_port(self) -> None:
        """Configure submission port (587) in Postfix master.cf for mail clients."""
        if not self.POSTFIX_MASTER_CF.exists():
            return

        master_cf_content = self.POSTFIX_MASTER_CF.read_text()

        # Check if submission is already configured (uncommented)
        if "\nsubmission inet" in master_cf_content:
            return

        # Add submission port configuration
        submission_config = """
# Submission port for mail clients (added by HostKit)
submission inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_tls_auth_only=yes
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
  -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
  -o milter_macro_daemon_name=ORIGINATING
"""
        with open(self.POSTFIX_MASTER_CF, "a") as f:
            f.write(submission_config)

    def _configure_firewall(self) -> dict[str, Any]:
        """Configure firewall to allow mail ports (993 IMAPS, 587 submission)."""
        results = {
            "ports_opened": [],
            "errors": [],
        }

        # Check if ufw is available
        try:
            subprocess.run(["which", "ufw"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            results["errors"].append("ufw not found, skipping firewall configuration")
            return results

        # Open required mail ports
        mail_ports = [
            ("993/tcp", "IMAPS"),
            ("587/tcp", "SMTP Submission"),
        ]

        for port, description in mail_ports:
            try:
                result = subprocess.run(
                    ["ufw", "allow", port, "comment", description],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    results["ports_opened"].append(port)
                else:
                    results["errors"].append(f"Failed to open {port}: {result.stderr}")
            except Exception as e:
                results["errors"].append(f"Error opening {port}: {e}")

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Domain Management
    # ─────────────────────────────────────────────────────────────────────────

    def list_domains(self) -> list[MailDomain]:
        """List all configured mail domains."""
        mail_config = self._load_mail_config()
        domains = []

        for domain_name, domain_data in mail_config.get("domains", {}).items():
            # Get mailboxes for this domain
            mailboxes = [
                addr for addr, data in mail_config.get("mailboxes", {}).items()
                if data.get("domain") == domain_name
            ]

            domains.append(MailDomain(
                name=domain_name,
                dkim_enabled=domain_data.get("dkim_enabled", False),
                dkim_selector=domain_data.get("dkim_selector", "default"),
                created_at=domain_data.get("created_at", ""),
                mailboxes=mailboxes,
            ))

        return domains

    def add_domain(self, domain: str, selector: str = "default") -> dict[str, Any]:
        """Add a mail domain and generate DKIM keys."""
        mail_config = self._load_mail_config()

        if domain in mail_config.get("domains", {}):
            raise MailError(
                code="DOMAIN_EXISTS",
                message=f"Mail domain '{domain}' already configured",
            )

        # Add to virtual domains file
        with open(self.VIRTUAL_DOMAINS_FILE, "a") as f:
            f.write(f"{domain} OK\n")

        # Regenerate postmap db
        subprocess.run(["postmap", str(self.VIRTUAL_DOMAINS_FILE)], check=True)

        # Generate DKIM keys
        dkim_result = self._generate_dkim_keys(domain, selector)

        # Save to config
        import datetime
        if "domains" not in mail_config:
            mail_config["domains"] = {}

        mail_config["domains"][domain] = {
            "dkim_enabled": True,
            "dkim_selector": selector,
            "dkim_public_key": dkim_result["public_key"],
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save_mail_config(mail_config)

        # Reload services
        subprocess.run(["systemctl", "reload", "postfix"], check=False)
        subprocess.run(["systemctl", "reload", "opendkim"], check=False)

        # Generate DNS records
        dns_records = self.generate_dns_records(domain, dkim_result["public_key"])

        return {
            "domain": domain,
            "dkim_selector": selector,
            "dkim_public_key": dkim_result["public_key"],
            "dns_records": dns_records,
            "message": f"Mail domain '{domain}' configured",
        }

    def _generate_dkim_keys(self, domain: str, selector: str) -> dict[str, str]:
        """Generate DKIM key pair for a domain."""
        key_dir = self.DKIM_KEYS_DIR / domain
        key_dir.mkdir(parents=True, exist_ok=True)

        private_key_path = key_dir / f"{selector}.private"
        txt_record_path = key_dir / f"{selector}.txt"

        # Generate key pair
        subprocess.run(
            [
                "opendkim-genkey",
                "-b", "2048",
                "-d", domain,
                "-D", str(key_dir),
                "-s", selector,
                "-v",
            ],
            check=True,
            capture_output=True,
        )

        # Set permissions
        subprocess.run(
            ["chown", "-R", "opendkim:opendkim", str(key_dir)],
            check=False,
        )
        private_key_path.chmod(0o600)

        # Add to key table
        with open(self.DKIM_KEY_TABLE, "a") as f:
            f.write(f"{selector}._domainkey.{domain} {domain}:{selector}:{private_key_path}\n")

        # Add to signing table
        with open(self.DKIM_SIGNING_TABLE, "a") as f:
            f.write(f"*@{domain} {selector}._domainkey.{domain}\n")

        # Read public key from txt file
        public_key = ""
        if txt_record_path.exists():
            content = txt_record_path.read_text()
            # Extract just the key part from the TXT record format
            import re
            match = re.search(r'p=([A-Za-z0-9+/=]+)', content)
            if match:
                public_key = match.group(1)

        return {
            "private_key_path": str(private_key_path),
            "public_key": public_key,
        }

    def remove_domain(self, domain: str, force: bool = False) -> dict[str, Any]:
        """Remove a mail domain."""
        mail_config = self._load_mail_config()

        if domain not in mail_config.get("domains", {}):
            raise MailError(
                code="DOMAIN_NOT_FOUND",
                message=f"Mail domain '{domain}' not found",
            )

        # Check for mailboxes
        mailboxes = [
            addr for addr, data in mail_config.get("mailboxes", {}).items()
            if data.get("domain") == domain
        ]

        if mailboxes and not force:
            raise MailError(
                code="DOMAIN_HAS_MAILBOXES",
                message=f"Domain '{domain}' has {len(mailboxes)} mailbox(es)",
                suggestion="Use --force to delete domain and all mailboxes",
            )

        # Remove mailboxes
        for addr in mailboxes:
            del mail_config["mailboxes"][addr]

        # Remove from virtual domains file
        if self.VIRTUAL_DOMAINS_FILE.exists():
            lines = self.VIRTUAL_DOMAINS_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{domain} ")]
            self.VIRTUAL_DOMAINS_FILE.write_text("\n".join(lines) + "\n" if lines else "")
            subprocess.run(["postmap", str(self.VIRTUAL_DOMAINS_FILE)], check=False)

        # Remove DKIM entries (cleanup)
        key_dir = self.DKIM_KEYS_DIR / domain
        if key_dir.exists():
            import shutil
            shutil.rmtree(key_dir)

        # Remove from config
        del mail_config["domains"][domain]
        self._save_mail_config(mail_config)

        # Reload services
        subprocess.run(["systemctl", "reload", "postfix"], check=False)
        subprocess.run(["systemctl", "reload", "opendkim"], check=False)

        return {
            "domain": domain,
            "mailboxes_removed": len(mailboxes),
            "message": f"Mail domain '{domain}' removed",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Mailbox Management
    # ─────────────────────────────────────────────────────────────────────────

    def list_mailboxes(self, domain: str | None = None) -> list[Mailbox]:
        """List all mailboxes, optionally filtered by domain."""
        mail_config = self._load_mail_config()
        mailboxes = []

        for address, data in mail_config.get("mailboxes", {}).items():
            if domain and data.get("domain") != domain:
                continue

            mailboxes.append(Mailbox(
                address=address,
                domain=data.get("domain", ""),
                local_part=data.get("local_part", ""),
                project=data.get("project", ""),
                maildir=data.get("maildir", ""),
                created_at=data.get("created_at", ""),
            ))

        return mailboxes

    def add_mailbox(
        self,
        address: str,
        project: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Create a virtual mailbox for a project."""
        mail_config = self._load_mail_config()

        if address in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_EXISTS",
                message=f"Mailbox '{address}' already exists",
            )

        # Parse address
        if "@" not in address:
            raise MailError(
                code="INVALID_ADDRESS",
                message=f"Invalid email address: {address}",
                suggestion="Use format: user@domain.com",
            )

        local_part, domain = address.split("@", 1)

        # Check domain is configured
        if domain not in mail_config.get("domains", {}):
            raise MailError(
                code="DOMAIN_NOT_CONFIGURED",
                message=f"Mail domain '{domain}' is not configured",
                suggestion=f"Run 'hostkit mail add-domain {domain}' first",
            )

        # Check project exists
        from hostkit.database import get_db
        db = get_db()
        proj = db.get_project(project)
        if not proj:
            raise MailError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Generate password if not provided
        if not password:
            password = secrets.token_urlsafe(16)

        # Create password hash for Dovecot
        result = subprocess.run(
            ["doveadm", "pw", "-s", "SHA512-CRYPT", "-p", password],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MailError(
                code="PASSWORD_HASH_FAILED",
                message="Failed to generate password hash",
            )
        password_hash = result.stdout.strip()

        # Add to Dovecot users file
        with open(self.DOVECOT_USERS_FILE, "a") as f:
            f.write(f"{address}:{password_hash}\n")

        # Maildir path
        maildir = f"{self.MAILDIR_BASE}/{domain}/{local_part}"

        # Add to virtual mailbox maps
        with open(self.VIRTUAL_MAILBOX_FILE, "a") as f:
            f.write(f"{address} {domain}/{local_part}/\n")

        # Regenerate postmap db
        subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=True)

        # Create Maildir structure
        maildir_path = Path(maildir)
        for subdir in ["", "cur", "new", "tmp"]:
            (maildir_path / subdir).mkdir(parents=True, exist_ok=True)

        # Set ownership
        subprocess.run(
            ["chown", "-R", f"{self.VMAIL_UID}:{self.VMAIL_GID}", maildir],
            check=True,
        )

        # Save to config
        import datetime
        if "mailboxes" not in mail_config:
            mail_config["mailboxes"] = {}

        mail_config["mailboxes"][address] = {
            "domain": domain,
            "local_part": local_part,
            "project": project,
            "maildir": maildir,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save_mail_config(mail_config)

        # Reload Postfix
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "address": address,
            "project": project,
            "password": password,
            "maildir": maildir,
            "imap_server": f"mail.{domain}",
            "imap_port": 993,
            "smtp_server": f"mail.{domain}",
            "smtp_port": 587,
            "message": f"Mailbox '{address}' created",
        }

    def remove_mailbox(self, address: str) -> dict[str, Any]:
        """Remove a virtual mailbox."""
        mail_config = self._load_mail_config()

        if address not in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_NOT_FOUND",
                message=f"Mailbox '{address}' not found",
            )

        mailbox_data = mail_config["mailboxes"][address]

        # Remove from Dovecot users
        if self.DOVECOT_USERS_FILE.exists():
            lines = self.DOVECOT_USERS_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{address}:")]
            self.DOVECOT_USERS_FILE.write_text("\n".join(lines) + "\n" if lines else "")

        # Remove from virtual mailbox maps
        if self.VIRTUAL_MAILBOX_FILE.exists():
            lines = self.VIRTUAL_MAILBOX_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{address} ")]
            self.VIRTUAL_MAILBOX_FILE.write_text("\n".join(lines) + "\n" if lines else "")
            subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=False)

        # Remove from config (keep maildir for now - manual cleanup)
        del mail_config["mailboxes"][address]
        self._save_mail_config(mail_config)

        # Reload services
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "address": address,
            "maildir": mailbox_data.get("maildir"),
            "message": f"Mailbox '{address}' removed (maildir preserved)",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Mail Queue Management
    # ─────────────────────────────────────────────────────────────────────────

    def get_queue(self) -> dict[str, Any]:
        """Get mail queue status and entries."""
        # Check if mailq is available
        if not self.is_installed():
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix is not installed",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        # Get queue summary using mailq
        try:
            result = subprocess.run(
                ["mailq"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix mailq command not found",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        if "Mail queue is empty" in result.stdout:
            return {
                "count": 0,
                "entries": [],
                "total_size": "0",
            }

        entries = []
        lines = result.stdout.strip().split("\n")

        # Parse mailq output
        current_entry = None
        for line in lines:
            if not line.strip() or line.startswith("-"):
                continue

            # Queue entries start with queue ID (12 char hex)
            if len(line) > 0 and not line.startswith(" "):
                parts = line.split()
                if len(parts) >= 4:
                    # Format: QUEUE_ID SIZE ARRIVAL_TIME SENDER
                    if current_entry:
                        entries.append(current_entry)

                    current_entry = {
                        "queue_id": parts[0].rstrip("*!"),
                        "size": parts[1],
                        "arrival_time": " ".join(parts[2:5]) if len(parts) > 4 else parts[2],
                        "sender": parts[-1] if len(parts) > 4 else "",
                        "recipients": [],
                        "status": "active" if "*" in parts[0] else "deferred" if "!" in parts[0] else "queued",
                    }
            elif current_entry and line.strip():
                # Recipient lines are indented
                if line.strip().startswith("("):
                    # Error message, skip
                    pass
                else:
                    current_entry["recipients"].append(line.strip())

        if current_entry:
            entries.append(current_entry)

        return {
            "count": len(entries),
            "entries": entries,
            "total_size": sum(int(e.get("size", "0")) for e in entries if e.get("size", "").isdigit()),
        }

    def flush_queue(self, queue_id: str | None = None) -> dict[str, Any]:
        """Flush mail queue (force delivery attempts)."""
        if not self.is_installed():
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix is not installed",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        try:
            if queue_id:
                # Flush specific message
                result = subprocess.run(
                    ["postqueue", "-i", queue_id],
                    capture_output=True,
                    text=True,
                )
            else:
                # Flush entire queue
                result = subprocess.run(
                    ["postqueue", "-f"],
                    capture_output=True,
                    text=True,
                )
        except FileNotFoundError:
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix postqueue command not found",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        return {
            "flushed": True,
            "queue_id": queue_id or "all",
            "message": "Mail queue flush initiated",
        }

    def delete_queue_entry(self, queue_id: str) -> dict[str, Any]:
        """Delete a specific queue entry."""
        if not self.is_installed():
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix is not installed",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        try:
            result = subprocess.run(
                ["postsuper", "-d", queue_id],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix postsuper command not found",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        if result.returncode != 0:
            raise MailError(
                code="DELETE_FAILED",
                message=f"Failed to delete queue entry: {result.stderr}",
            )

        return {
            "deleted": True,
            "queue_id": queue_id,
            "message": f"Queue entry '{queue_id}' deleted",
        }

    def purge_queue(self) -> dict[str, Any]:
        """Delete all entries from the mail queue."""
        if not self.is_installed():
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix is not installed",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        try:
            result = subprocess.run(
                ["postsuper", "-d", "ALL"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise MailError(
                code="NOT_INSTALLED",
                message="Postfix postsuper command not found",
                suggestion="Run 'hostkit mail setup --hostname mail.example.com' first",
            )

        return {
            "purged": True,
            "message": "All queue entries deleted",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # DNS Record Generation
    # ─────────────────────────────────────────────────────────────────────────

    def generate_dns_records(
        self,
        domain: str,
        dkim_public_key: str | None = None,
    ) -> dict[str, dict[str, str]]:
        """Generate DNS records required for mail."""
        # Get VPS IP
        from hostkit.services.dns_service import DNSService
        dns_service = DNSService()
        vps_ip = dns_service.get_vps_ip()

        mail_config = self._load_mail_config()
        domain_config = mail_config.get("domains", {}).get(domain, {})

        if not dkim_public_key:
            dkim_public_key = domain_config.get("dkim_public_key", "")

        selector = domain_config.get("dkim_selector", "default")

        records = {
            "mx": {
                "name": domain,
                "type": "MX",
                "content": f"10 mail.{domain}",
                "ttl": "3600",
                "description": "Mail server",
            },
            "mail_a": {
                "name": f"mail.{domain}",
                "type": "A",
                "content": vps_ip,
                "ttl": "3600",
                "description": "Mail server IP",
            },
            "spf": {
                "name": domain,
                "type": "TXT",
                "content": f"v=spf1 ip4:{vps_ip} mx -all",
                "ttl": "3600",
                "description": "SPF record",
            },
            "dmarc": {
                "name": f"_dmarc.{domain}",
                "type": "TXT",
                "content": f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{domain}",
                "ttl": "3600",
                "description": "DMARC policy",
            },
        }

        if dkim_public_key:
            records["dkim"] = {
                "name": f"{selector}._domainkey.{domain}",
                "type": "TXT",
                "content": f"v=DKIM1; k=rsa; p={dkim_public_key}",
                "ttl": "3600",
                "description": "DKIM public key",
            }

        return records

    def get_domain_dns_status(self, domain: str) -> dict[str, Any]:
        """Check DNS records for a mail domain."""
        import subprocess

        required_records = self.generate_dns_records(domain)
        status = {}

        for name, record_info in required_records.items():
            record_name = record_info["name"]
            record_type = record_info["type"]

            try:
                result = subprocess.run(
                    ["dig", "+short", record_type, record_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                found = result.stdout.strip()
                status[name] = {
                    "name": record_name,
                    "type": record_type,
                    "expected": record_info["content"],
                    "found": found if found else None,
                    "ok": bool(found),
                }
            except Exception:
                status[name] = {
                    "name": record_name,
                    "type": record_type,
                    "expected": record_info["content"],
                    "found": None,
                    "ok": False,
                }

        return {
            "domain": domain,
            "records": status,
            "all_ok": all(r["ok"] for r in status.values()),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Project-Scoped Mail Management
    # ─────────────────────────────────────────────────────────────────────────

    # Base domain for project mail
    PROJECT_MAIL_DOMAIN = "hostkit.dev"

    def _get_project_mail_domain(self, project: str) -> str:
        """Get the mail subdomain for a project."""
        return f"{project}.{self.PROJECT_MAIL_DOMAIN}"

    def _verify_project_exists(self, project: str) -> dict[str, Any]:
        """Verify a project exists and return its info."""
        from hostkit.database import get_db
        db = get_db()
        proj = db.get_project(project)
        if not proj:
            raise MailError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return proj

    def is_project_mail_enabled(self, project: str) -> bool:
        """Check if mail is enabled for a project."""
        mail_config = self._load_mail_config()
        domain = self._get_project_mail_domain(project)
        return domain in mail_config.get("domains", {})

    def enable_project_mail(self, project: str) -> dict[str, Any]:
        """Enable mail for a project.

        Creates a subdomain under hostkit.dev and a default noreply address.
        """
        # Verify project exists
        self._verify_project_exists(project)

        # Check if mail server is set up
        if not self.is_installed():
            raise MailError(
                code="MAIL_NOT_INSTALLED",
                message="Mail server is not installed",
                suggestion="Contact admin to run 'hostkit mail setup'",
            )

        domain = self._get_project_mail_domain(project)
        mail_config = self._load_mail_config()

        # Check if already enabled
        if domain in mail_config.get("domains", {}):
            raise MailError(
                code="MAIL_ALREADY_ENABLED",
                message=f"Mail is already enabled for '{project}'",
                suggestion=f"Run 'hostkit mail list {project}' to see mailboxes",
            )

        # Check if hostkit.dev is configured as the parent domain
        # If not, we need to add it first (requires admin)
        parent_domain = self.PROJECT_MAIL_DOMAIN
        if parent_domain not in mail_config.get("domains", {}):
            raise MailError(
                code="PARENT_DOMAIN_NOT_CONFIGURED",
                message=f"Parent domain '{parent_domain}' is not configured for mail",
                suggestion="Contact admin to run 'hostkit mail add-domain hostkit.dev'",
            )

        # Add the subdomain to virtual domains
        with open(self.VIRTUAL_DOMAINS_FILE, "a") as f:
            f.write(f"{domain} OK\n")

        # Regenerate postmap db
        subprocess.run(["postmap", str(self.VIRTUAL_DOMAINS_FILE)], check=True)

        # Save to config (subdomains inherit parent's DKIM)
        import datetime
        if "domains" not in mail_config:
            mail_config["domains"] = {}

        mail_config["domains"][domain] = {
            "dkim_enabled": True,  # Uses parent domain's DKIM
            "dkim_selector": "default",
            "parent_domain": parent_domain,
            "project": project,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save_mail_config(mail_config)

        # Reload Postfix
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        # Create default noreply mailbox
        default_address = f"noreply@{domain}"
        password = secrets.token_urlsafe(16)

        # Create password hash for Dovecot
        result = subprocess.run(
            ["doveadm", "pw", "-s", "SHA512-CRYPT", "-p", password],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MailError(
                code="PASSWORD_HASH_FAILED",
                message="Failed to generate password hash",
            )
        password_hash = result.stdout.strip()

        # Add to Dovecot users file
        with open(self.DOVECOT_USERS_FILE, "a") as f:
            f.write(f"{default_address}:{password_hash}\n")

        # Maildir path
        maildir = f"{self.MAILDIR_BASE}/{domain}/noreply"

        # Add to virtual mailbox maps
        with open(self.VIRTUAL_MAILBOX_FILE, "a") as f:
            f.write(f"{default_address} {domain}/noreply/\n")

        # Regenerate postmap db
        subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=True)

        # Create Maildir structure
        maildir_path = Path(maildir)
        for subdir in ["", "cur", "new", "tmp"]:
            (maildir_path / subdir).mkdir(parents=True, exist_ok=True)

        # Set ownership
        subprocess.run(
            ["chown", "-R", f"{self.VMAIL_UID}:{self.VMAIL_GID}", maildir],
            check=True,
        )

        # Save mailbox to config
        import datetime
        if "mailboxes" not in mail_config:
            mail_config["mailboxes"] = {}

        mail_config["mailboxes"][default_address] = {
            "domain": domain,
            "local_part": "noreply",
            "project": project,
            "maildir": maildir,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save_mail_config(mail_config)

        # Reload Postfix
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "project": project,
            "domain": domain,
            "default_address": default_address,
            "password": password,
            "maildir": maildir,
            "message": f"Mail enabled for '{project}'",
        }

    def disable_project_mail(self, project: str, force: bool = False) -> dict[str, Any]:
        """Disable mail for a project.

        Removes the subdomain and all associated mailboxes.
        """
        domain = self._get_project_mail_domain(project)
        mail_config = self._load_mail_config()

        if domain not in mail_config.get("domains", {}):
            raise MailError(
                code="MAIL_NOT_ENABLED",
                message=f"Mail is not enabled for '{project}'",
            )

        # Get mailboxes for this domain
        mailboxes = [
            addr for addr, data in mail_config.get("mailboxes", {}).items()
            if data.get("domain") == domain
        ]

        if mailboxes and not force:
            raise MailError(
                code="PROJECT_HAS_MAILBOXES",
                message=f"Project '{project}' has {len(mailboxes)} mailbox(es)",
                suggestion="Use --force to remove domain and all mailboxes",
            )

        # Remove mailboxes from Dovecot users
        if self.DOVECOT_USERS_FILE.exists():
            lines = self.DOVECOT_USERS_FILE.read_text().splitlines()
            lines = [l for l in lines if not any(l.startswith(f"{addr}:") for addr in mailboxes)]
            self.DOVECOT_USERS_FILE.write_text("\n".join(lines) + "\n" if lines else "")

        # Remove mailboxes from virtual mailbox maps
        if self.VIRTUAL_MAILBOX_FILE.exists():
            lines = self.VIRTUAL_MAILBOX_FILE.read_text().splitlines()
            lines = [l for l in lines if not any(l.startswith(f"{addr} ") for addr in mailboxes)]
            self.VIRTUAL_MAILBOX_FILE.write_text("\n".join(lines) + "\n" if lines else "")
            subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=False)

        # Remove from virtual domains file
        if self.VIRTUAL_DOMAINS_FILE.exists():
            lines = self.VIRTUAL_DOMAINS_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{domain} ")]
            self.VIRTUAL_DOMAINS_FILE.write_text("\n".join(lines) + "\n" if lines else "")
            subprocess.run(["postmap", str(self.VIRTUAL_DOMAINS_FILE)], check=False)

        # Remove mailboxes from config
        for addr in mailboxes:
            del mail_config["mailboxes"][addr]

        # Remove domain from config
        del mail_config["domains"][domain]
        self._save_mail_config(mail_config)

        # Reload services
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "project": project,
            "domain": domain,
            "mailboxes_removed": len(mailboxes),
            "message": f"Mail disabled for '{project}'",
        }

    def add_project_mailbox(
        self,
        project: str,
        local_part: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Add a mailbox for a project.

        Creates a mailbox using the project's subdomain.
        """
        domain = self._get_project_mail_domain(project)
        mail_config = self._load_mail_config()

        # Check mail is enabled for project
        if domain not in mail_config.get("domains", {}):
            raise MailError(
                code="MAIL_NOT_ENABLED",
                message=f"Mail is not enabled for '{project}'",
                suggestion=f"Run 'hostkit mail enable {project}' first",
            )

        address = f"{local_part}@{domain}"

        # Check mailbox doesn't already exist
        if address in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_EXISTS",
                message=f"Mailbox '{address}' already exists",
            )

        # Generate password if not provided
        if not password:
            password = secrets.token_urlsafe(16)

        # Create password hash for Dovecot
        result = subprocess.run(
            ["doveadm", "pw", "-s", "SHA512-CRYPT", "-p", password],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MailError(
                code="PASSWORD_HASH_FAILED",
                message="Failed to generate password hash",
            )
        password_hash = result.stdout.strip()

        # Add to Dovecot users file
        with open(self.DOVECOT_USERS_FILE, "a") as f:
            f.write(f"{address}:{password_hash}\n")

        # Maildir path
        maildir = f"{self.MAILDIR_BASE}/{domain}/{local_part}"

        # Add to virtual mailbox maps
        with open(self.VIRTUAL_MAILBOX_FILE, "a") as f:
            f.write(f"{address} {domain}/{local_part}/\n")

        # Regenerate postmap db
        subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=True)

        # Create Maildir structure
        maildir_path = Path(maildir)
        for subdir in ["", "cur", "new", "tmp"]:
            (maildir_path / subdir).mkdir(parents=True, exist_ok=True)

        # Set ownership
        subprocess.run(
            ["chown", "-R", f"{self.VMAIL_UID}:{self.VMAIL_GID}", maildir],
            check=True,
        )

        # Save to config
        import datetime
        if "mailboxes" not in mail_config:
            mail_config["mailboxes"] = {}

        mail_config["mailboxes"][address] = {
            "domain": domain,
            "local_part": local_part,
            "project": project,
            "maildir": maildir,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save_mail_config(mail_config)

        # Reload Postfix
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "address": address,
            "project": project,
            "password": password,
            "maildir": maildir,
            "message": f"Mailbox '{address}' created",
        }

    def remove_project_mailbox(self, project: str, local_part: str) -> dict[str, Any]:
        """Remove a mailbox from a project."""
        domain = self._get_project_mail_domain(project)
        address = f"{local_part}@{domain}"
        mail_config = self._load_mail_config()

        if address not in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_NOT_FOUND",
                message=f"Mailbox '{address}' not found",
            )

        mailbox_data = mail_config["mailboxes"][address]

        # Verify mailbox belongs to this project
        if mailbox_data.get("project") != project:
            raise MailError(
                code="MAILBOX_NOT_OWNED",
                message=f"Mailbox '{address}' does not belong to project '{project}'",
            )

        # Remove from Dovecot users
        if self.DOVECOT_USERS_FILE.exists():
            lines = self.DOVECOT_USERS_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{address}:")]
            self.DOVECOT_USERS_FILE.write_text("\n".join(lines) + "\n" if lines else "")

        # Remove from virtual mailbox maps
        if self.VIRTUAL_MAILBOX_FILE.exists():
            lines = self.VIRTUAL_MAILBOX_FILE.read_text().splitlines()
            lines = [l for l in lines if not l.startswith(f"{address} ")]
            self.VIRTUAL_MAILBOX_FILE.write_text("\n".join(lines) + "\n" if lines else "")
            subprocess.run(["postmap", str(self.VIRTUAL_MAILBOX_FILE)], check=False)

        # Remove from config (keep maildir for now)
        del mail_config["mailboxes"][address]
        self._save_mail_config(mail_config)

        # Reload services
        subprocess.run(["systemctl", "reload", "postfix"], check=False)

        return {
            "address": address,
            "maildir": mailbox_data.get("maildir"),
            "message": f"Mailbox '{address}' removed (maildir preserved)",
        }

    def list_project_mailboxes(self, project: str) -> dict[str, Any]:
        """List all mailboxes for a project."""
        domain = self._get_project_mail_domain(project)
        mail_config = self._load_mail_config()

        enabled = domain in mail_config.get("domains", {})

        mailboxes = []
        for address, data in mail_config.get("mailboxes", {}).items():
            if data.get("domain") == domain:
                mailboxes.append({
                    "address": address,
                    "local_part": data.get("local_part", ""),
                    "maildir": data.get("maildir", ""),
                    "created_at": data.get("created_at", ""),
                })

        return {
            "project": project,
            "domain": domain,
            "enabled": enabled,
            "mailboxes": mailboxes,
        }

    def get_project_credentials(
        self,
        project: str,
        local_part: str | None = None,
        reset_password: bool = False,
    ) -> dict[str, Any]:
        """Get or reset credentials for a project mailbox."""
        domain = self._get_project_mail_domain(project)

        if local_part is None:
            local_part = "noreply"

        address = f"{local_part}@{domain}"
        mail_config = self._load_mail_config()

        if address not in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_NOT_FOUND",
                message=f"Mailbox '{address}' not found",
                suggestion=f"Run 'hostkit mail add {project} {local_part}' to create it",
            )

        mailbox_data = mail_config["mailboxes"][address]

        # Verify mailbox belongs to this project
        if mailbox_data.get("project") != project:
            raise MailError(
                code="MAILBOX_NOT_OWNED",
                message=f"Mailbox '{address}' does not belong to project '{project}'",
            )

        result = {
            "address": address,
            "project": project,
            "smtp_server": "mail.hostkit.dev",
            "smtp_port": 587,
            "imap_server": "mail.hostkit.dev",
            "imap_port": 993,
        }

        if reset_password:
            # Generate new password
            new_password = secrets.token_urlsafe(16)

            # Create password hash for Dovecot
            hash_result = subprocess.run(
                ["doveadm", "pw", "-s", "SHA512-CRYPT", "-p", new_password],
                capture_output=True,
                text=True,
            )
            if hash_result.returncode != 0:
                raise MailError(
                    code="PASSWORD_HASH_FAILED",
                    message="Failed to generate password hash",
                )
            password_hash = hash_result.stdout.strip()

            # Update Dovecot users file
            if self.DOVECOT_USERS_FILE.exists():
                lines = self.DOVECOT_USERS_FILE.read_text().splitlines()
                new_lines = []
                for line in lines:
                    if line.startswith(f"{address}:"):
                        new_lines.append(f"{address}:{password_hash}")
                    else:
                        new_lines.append(line)
                self.DOVECOT_USERS_FILE.write_text("\n".join(new_lines) + "\n")

            result["password"] = new_password

        return result

    def send_test_email(
        self,
        project: str,
        to_email: str,
        from_local: str = "noreply",
    ) -> dict[str, Any]:
        """Send a test email from a project mailbox."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        domain = self._get_project_mail_domain(project)
        from_address = f"{from_local}@{domain}"
        mail_config = self._load_mail_config()

        # Verify mailbox exists
        if from_address not in mail_config.get("mailboxes", {}):
            raise MailError(
                code="MAILBOX_NOT_FOUND",
                message=f"Mailbox '{from_address}' not found",
                suggestion=f"Run 'hostkit mail add {project} {from_local}' to create it",
            )

        # Create message
        subject = f"Test Email from {project}"
        body = f"""This is a test email from HostKit.

Project: {project}
From: {from_address}
To: {to_email}

If you received this email, your mail configuration is working correctly.

--
Sent by HostKit Mail Service
"""

        msg = MIMEMultipart()
        msg["From"] = from_address
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            # Send via local Postfix (no auth needed for local delivery)
            with smtplib.SMTP("localhost", 25) as smtp:
                smtp.send_message(msg)
        except Exception as e:
            raise MailError(
                code="SEND_FAILED",
                message=f"Failed to send test email: {e}",
                suggestion="Check Postfix status with 'hostkit mail status'",
            )

        return {
            "from": from_address,
            "to": to_email,
            "subject": subject,
            "message": "Test email sent",
        }
