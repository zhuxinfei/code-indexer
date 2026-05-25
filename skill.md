---
name: code-indexer
description: Fast code exploration via pre-built knowledge graph. Use code_index_search/callers/callees/impact instead of grep/Explore sub-agents. Requires MCP server running.
metadata:
  type: reference
---

# Code Indexer — Local Code Graph

Project code is pre-indexed into a SQLite knowledge graph. Always check the index before spawning Explore agents or running grep.

## Core Rule

**1 MCP call replaces 5-15 grep/glob/Read + Explore agent.**

When entering a project, check if the index exists (`code_index_stats`). If not, build it first.

## Tool Selection

### Find a function/class/variable → `code_index_search`

```
"Where is handleClick defined?"
→ code_index_search(name="handleClick")
→ Returns [{name, kind, file, line}] — read the file at that line.
```

Never grep for symbol definitions. Search the index.

### Understand impact → `code_index_impact`

```
"What will break if I change fetchData?"
→ code_index_impact(name="fetchData")
→ Returns callers (upstream) + callees (downstream)
```

Never manually grep call chains. One impact call covers both directions.

### Trace callers or callees → `code_index_callers` / `code_index_callees`

```
"Who calls parseConfig?"
→ code_index_callers(name="parseConfig")

"What does parseConfig call internally?"
→ code_index_callees(name="parseConfig")
```

## Fallback Rules

Fall back to direct grep/Read only when:
- `code_index_stats` returns an error (no index exists)
- `code_index_search` returns empty (symbol renamed/deleted)
- The returned line number doesn't match the actual file (index is stale)
- You need to read function body content (Read the file, but use search to find the line first)

## Initialization

If no index exists or it's stale:
1. Call `code_index_init()` (no arguments, uses current directory)
2. Wait for completion (~5 seconds for 200 files)
3. Check `code_index_stats()` to confirm
4. Then query as needed

## Performance

| Project size | Initial index | Query latency |
|-------------|---------------|---------------|
| ~50 files | < 2 seconds | < 10ms |
| ~200 files | < 5 seconds | < 10ms |
| ~1000 files | < 20 seconds | < 20ms |

The index is rebuilt on demand — call `code_index_init` when you know the codebase has changed significantly.
