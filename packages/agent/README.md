# HostKit Agent Template

Starter configuration for Claude Code agents that manage HostKit projects. Contains the `CLAUDE.md` identity document, permission settings, and skill commands.

## Contents

| File | Purpose | Copy to |
|------|---------|---------|
| `CLAUDE.md.template` | Agent identity, MCP tool docs, architecture reference | Project root as `CLAUDE.md` |
| `claude-settings.json` | Tool and command permissions | `.claude/settings.local.json` |
| `commands/build.md` | Build orchestration skill | `.claude/commands/build.md` |
| `commands/mailbox.md` | Mailbox provisioning skill | `.claude/commands/mailbox.md` |

## Setup

### Automated (recommended)

The [local setup script](../../install/setup-local.sh) copies and configures everything:

```bash
bash install/setup-local.sh --vps-ip YOUR_VPS_IP
```

### Manual

1. Copy `CLAUDE.md.template` to your project root as `CLAUDE.md`
2. Replace all `{{VPS_IP}}` placeholders with your server's IP address
3. Copy `claude-settings.json` to `.claude/settings.local.json`
4. Replace `YOUR_VPS_IP` in the settings file with your server's IP
5. Copy `commands/` to `.claude/commands/`

## What's in CLAUDE.md.template

The template defines the agent's full operating context:

- **Identity** -- what the agent is and what it's responsible for
- **MCP tool reference** -- all 15 tools with parameters, examples, and expert tips
- **Workflows** -- step-by-step patterns for discovery, deployment, troubleshooting, and service enablement
- **Architecture** -- project layout, access tiers, service port allocation, OAuth flow
- **Auth integration guide** -- critical fixes for JWT keys, token handling, and browser compatibility
- **Quick reference** -- common commands and service ports at a glance

## Customizing CLAUDE.md

The template is meant to be read and edited. Common customizations:

- **Identity section** -- change the agent's role description to match your use case
- **Safety constraints** -- adjust what the agent is and isn't allowed to do
- **Service documentation** -- add notes about your specific service configurations
- **Workflow patterns** -- add project-specific deployment or troubleshooting flows

## Customizing permissions

The `claude-settings.json` file controls what tools and commands the agent can use without asking for confirmation. The default permissions allow:

- SSH and file transfer commands
- All 15 HostKit MCP tools
- Build commands (`npm run build`)
- Network tools (`curl`, `dig`)
- Web search

To restrict an agent (e.g., a project-scoped agent that shouldn't execute arbitrary commands), remove permissions from the `allow` list.

## Adding custom skills

Skills are markdown files in `.claude/commands/` that define reusable agent workflows. To add a new skill:

1. Create a markdown file in `commands/` (e.g., `commands/my-skill.md`)
2. Write the skill prompt -- this becomes the system instruction when invoked
3. Copy to `.claude/commands/` in your project

Invoke skills in Claude Code with `/my-skill`.

## Project agent vs substrate agent

This template is designed for the **substrate agent** -- the central intelligence that manages the VPS and all projects. Project-specific agents should use a stripped-down version with:

- Only their project's context in CLAUDE.md
- `user: "project"` in MCP tool calls (restricts to own project)
- Fewer permissions (no `hostkit_execute` for arbitrary commands)
