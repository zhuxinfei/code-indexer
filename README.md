# Code Indexer

100% local code knowledge graph MCP server for Claude Code. Pre-indexes your codebase so AI agents find symbols instantly — no more spawning Explore sub-agents to grep/glob/Read every file.

## Why

Claude Code's Explore agent burns tokens scanning files one by one. Code Indexer builds a SQLite symbol graph upfront so the agent can answer "where is this function?" and "who calls this?" in a single MCP call.

| | Without | With Code Indexer |
|---|---|---|
| Find a function | 5-15 grep/glob/Read calls + Explore agent | 1 `code_index_search` call |
| Impact analysis | Manual grep across the codebase | 1 `code_index_impact` call |
| Call chain tracing | Multiple Explore rounds | 1 `code_index_callers` call |

## Features

- **Symbol search** — find functions, classes, variables by name with FTS5 full-text + prefix matching
- **Call graph** — trace callers, callees, and full impact radius of any symbol
- **5 languages** — JavaScript/TypeScript, Python, Go, Vue SFC
- **Zero dependencies beyond `mcp` SDK** — uses Python stdlib sqlite3
- **Zero network calls** — no telemetry, no API keys, no external services
- **Inspectable** — single Python file, readable in 5 minutes
- **Lightweight** — indexes 200 files in under 5 seconds

## Install

```bash
pip install code-indexer
```

Or from source:

```bash
git clone https://github.com/YOUR_USER/code-indexer.git
cd code-indexer
pip install .
```

## Setup (once)

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "code-indexer": {
      "type": "stdio",
      "command": "code-indexer"
    }
  }
}
```

Restart Claude Code.

## Usage

In any Claude Code session, just ask naturally:

> "Index this project"

Agent calls `code_index_init`, builds the index in `.code-index/codegraph.db`.

Then:

> "Where is `requestDeepSeekChat` defined?"
> → Agent calls `code_index_search(name="requestDeepSeekChat")` → returns `worker/index.js:60`

> "What breaks if I change `buildCard`?"
> → Agent calls `code_index_impact(name="buildCard")` → returns all callers and callees

> "How big is the index?"
> → Agent calls `code_index_stats()` → symbols, files, languages breakdown

## MCP Tools

| Tool | Description |
|------|-------------|
| `code_index_search` | Search symbols by name |
| `code_index_callers` | Find who calls a symbol |
| `code_index_callees` | Find what a symbol calls |
| `code_index_impact` | Full impact analysis (callers + callees) |
| `code_index_init` | Build/rebuild the index |
| `code_index_stats` | Index statistics |

## Supported Languages

| Language | Extensions |
|----------|-----------|
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| TypeScript | `.ts`, `.tsx` |
| Python | `.py` |
| Go | `.go` |
| Vue | `.vue` |

## Cleanup

```bash
rm -rf .code-index/          # Remove index from a project
pip uninstall code-indexer   # Remove the tool
```

## Security

- No network calls anywhere in the code
- No telemetry, no Sentry, no analytics
- All data stays in `.code-index/codegraph.db` in your project
- Single-file Python — audit it yourself in 5 minutes

## License

MIT
