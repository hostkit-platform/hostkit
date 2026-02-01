# Contributing to HostKit

HostKit is a monorepo with three packages. Each can be developed independently.

## Repository structure

```
packages/
├── cli/            # Python 3.11+ — runs on the VPS
├── mcp-server/     # TypeScript (Node 20+) — runs locally
└── agent/          # Markdown + JSON — agent configuration
```

## Development setup

### CLI (Python)

```bash
cd packages/cli
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
ruff check src/
mypy src/hostkit/
pytest
```

### MCP Server (TypeScript)

```bash
cd packages/mcp-server
npm install    # or pnpm install
npm run build
```

Verify:

```bash
npm run typecheck
npm test
```

### Embeddings (Python, MCP server dependency)

```bash
cd packages/mcp-server
pip install sentence-transformers
npm run build-embeddings
```

## Code style

### Python (CLI)

- Formatter/linter: **ruff**
- Line length: 100
- Target: Python 3.11
- Type checking: **mypy** (strict mode)
- Rules: `E`, `F`, `I`, `N`, `W`, `UP` (pycodestyle, pyflakes, isort, pep8-naming, warnings, pyupgrade)

```bash
# Check
ruff check src/

# Fix auto-fixable issues
ruff check --fix src/

# Type check
mypy src/hostkit/
```

### TypeScript (MCP Server)

- Compiler: **tsc** (strict mode)
- Target: ES2022
- Module: ESNext (ES modules)
- Test framework: **vitest**

```bash
# Type check
npm run typecheck

# Test
npm test
```

## Testing

### CLI

```bash
cd packages/cli
pytest                    # all tests
pytest tests/test_foo.py  # specific file
pytest -v                 # verbose
```

### MCP Server

```bash
cd packages/mcp-server
npm test
```

## Making changes

### CLI changes

1. Edit files in `packages/cli/src/hostkit/`
2. Run `ruff check` and `mypy`
3. Run `pytest`
4. Deploy to VPS: `VPS_HOST=root@YOUR_VPS_IP ./packages/cli/scripts/deploy.sh`

### MCP server changes

1. Edit files in `packages/mcp-server/src/`
2. Run `npm run build`
3. Run `npm run typecheck`
4. Run `npm test`
5. Restart the MCP server in Claude Code

### Agent template changes

1. Edit files in `packages/agent/`
2. Verify no hardcoded IPs or secrets (`grep -rn "145\." packages/agent/`)
3. Test by copying to a fresh project directory

### Adding a new CLI command

1. Create `packages/cli/src/hostkit/commands/mycommand.py`
2. Register it in `packages/cli/src/hostkit/cli.py`
3. If the command needs sudo access, update the sudoers template in `packages/cli/templates/operator-sudoers.j2`
4. Add tests in `packages/cli/tests/`

### Adding a new MCP tool

1. Create the handler in `packages/mcp-server/src/tools/mytools.ts`
2. Add the tool definition and handler case in `packages/mcp-server/src/tools/index.ts`
3. Add the permission to `packages/agent/claude-settings.json`
4. Document the tool in `packages/agent/CLAUDE.md.template`

## Pull request process

1. Create a feature branch from `main`
2. Make your changes following the code style guidelines
3. Run the relevant linters and tests
4. Ensure no secrets or hardcoded IPs are in the diff
5. Open a PR with a clear description of what changed and why

## Security

HostKit manages production infrastructure. Before submitting changes:

- Run the security grep from the root: `grep -rn "BEGIN PRIVATE\|BEGIN RSA\|AKIA\|sk-ant-\|sk-\|ghp_" packages/`
- Never commit `.env` files, private keys, or credentials
- Never hardcode IP addresses -- use configuration or placeholders
- Test permission changes carefully (sudoers errors can lock you out)
