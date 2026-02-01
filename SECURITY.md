# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in HostKit, please report it responsibly.

**Do not open a public issue.** Instead:

1. Use [GitHub Security Advisories](https://github.com/hostkit-platform/hostkit/security/advisories/new) to report privately
2. Or email security concerns to the maintainers via the contact in the repository profile

## What Counts as a Vulnerability

**In scope:**

- Unauthorized VPS access through the CLI or MCP server
- Credential leaks (SSH keys, API tokens, database passwords)
- Privilege escalation (project user gaining root or operator access)
- Command injection in the CLI or MCP server
- Authentication bypass in HostKit services (auth, payments, etc.)
- Secrets exposed in logs or error messages

**Out of scope:**

- Bugs in individual project application code (not HostKit itself)
- Issues requiring physical access to the VPS
- Social engineering
- Denial of service via resource exhaustion (use rate limiting)

## Response

We aim to acknowledge reports within 48 hours and provide a fix or mitigation plan within 7 days for confirmed vulnerabilities.

## Supported Versions

Security fixes are applied to the latest release only.
