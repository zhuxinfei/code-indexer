#!/usr/bin/env python3
"""
Code Indexer MCP Server
Pre-indexes codebases into SQLite with FTS5 for fast symbol search.
100% local, zero network calls, zero telemetry.
"""

import sqlite3
import re
import os
from pathlib import Path
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# ── Constants ─────────────────────────────────────────────────
SKIP_DIRS = {'.git', 'node_modules', 'dist', 'build', '__pycache__',
             '.next', 'vendor', '.wrangler', 'out', '.code-index',
             '.wrangler', 'cache', 'tmp', 'blobs'}
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg',
            '.woff', '.woff2', '.ttf', '.eot', '.map', '.lock',
            '.sqlite', '.sqlite-shm', '.sqlite-wal', '.db',
            '.pyc', '.class', '.o', '.a', '.so', '.dylib',
            '.zip', '.tar', '.gz', '.pdf', '.mp4', '.mp3'}
INDEX_DIR = '.code-index'
DB_NAME = 'codegraph.db'

# ── Language Patterns ─────────────────────────────────────────

def js_ts_patterns():
    """Return list of (kind, regex) for JavaScript/TypeScript."""
    ident = r'[a-zA-Z_$][\w$]*'
    return [
        # function name(...) / async function name(...)
        ('function',
         re.compile(rf'(?:async\s+)?function\s+({ident})\s*[<(]', re.MULTILINE)),
        # class Name
        ('class',
         re.compile(rf'class\s+({ident})\s*(?:extends|implements|\{{|$)', re.MULTILINE)),
        # const/let/var name = (...) => / function / ...
        ('arrow',
         re.compile(rf'(?:const|let|var)\s+({ident})\s*=\s*(?:async\s*)?[\(]', re.MULTILINE)),
        # const/let/var name =
        ('variable',
         re.compile(rf'(?:const|let|var)\s+({ident})\s*=', re.MULTILINE)),
        # method shorthand: name(...) {  (inside class/object body)
        ('method',
         re.compile(rf'^\s*(?:static\s+)?(?:async\s+)?({ident})\s*\(', re.MULTILINE)),
        # export const/function/class name
        ('export',
         re.compile(rf'export\s+(?:const|let|var|function|class|default\s+function|default\s+class)\s+({ident})', re.MULTILINE)),
        # TypeScript: interface / type / enum
        ('interface',
         re.compile(rf'interface\s+({ident})\b', re.MULTILINE)),
        ('type',
         re.compile(rf'type\s+({ident})\b', re.MULTILINE)),
        ('enum',
         re.compile(rf'enum\s+({ident})\b', re.MULTILINE)),
        # import statements — record as reference
        ('import',
         re.compile(rf'import\s+(?:\{{[^}}]*\}}|({ident}))\s+from', re.MULTILINE)),
    ]


def python_patterns():
    """Return list of (kind, regex) for Python."""
    ident = r'[a-zA-Z_]\w*'
    return [
        ('function',
         re.compile(rf'^\s*(?:async\s+)?def\s+({ident})\s*\(', re.MULTILINE)),
        ('class',
         re.compile(rf'^\s*class\s+({ident})\s*[:(]', re.MULTILINE)),
    ]


def go_patterns():
    """Return list of (kind, regex) for Go."""
    ident = r'[a-zA-Z_]\w*'
    return [
        # func Name(...) or func (r *Receiver) Name(...)
        ('function',
         re.compile(rf'func\s+(?:\({ident}\s+\*?{ident}\)\s+)?({ident})\s*\(', re.MULTILINE)),
        # type Name struct / interface
        ('type',
         re.compile(rf'type\s+({ident})\s+(?:struct|interface)\b', re.MULTILINE)),
    ]


# ── File Classification ───────────────────────────────────────

def classify_language(filepath: str) -> str | None:
    """Return language name or None if unsupported."""
    ext_map = {
        '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript',
        '.vue': 'vue',
        '.py': 'python',
        '.go': 'go',
        '.wxml': 'wxml', '.swan': 'swan',
    }
    ext = Path(filepath).suffix.lower()
    return ext_map.get(ext)


def should_skip(path: Path) -> bool:
    """Check if file/dir should be excluded."""
    parts = path.parts
    for part in parts:
        if part in SKIP_DIRS:
            return True
    if path.suffix.lower() in SKIP_EXT:
        return True
    return False


# ── Symbol Extraction ─────────────────────────────────────────

def extract_js_ts(content: str) -> list[tuple[str, str, int]]:
    """Extract (name, kind, line) from JS/TS content."""
    results = []
    for kind, pattern in js_ts_patterns():
        for m in pattern.finditer(content):
            name = m.group(1)
            if not name:
                continue
            # Filter keywords and common noise
            if name in ('if', 'else', 'for', 'while', 'do', 'return', 'await',
                        'async', 'typeof', 'instanceof', 'new', 'this', 'super',
                        'switch', 'case', 'catch', 'throw', 'try', 'finally',
                        'import', 'from', 'default', 'yield', 'let', 'const', 'var',
                        'get', 'set', 'of', 'in', 'void', 'delete', 'debugger'):
                continue
            if len(name) <= 1:
                continue
            line = content[:m.start()].count('\n') + 1
            results.append((name, kind, line))
    return results


def extract_python(content: str) -> list[tuple[str, str, int]]:
    """Extract (name, kind, line) from Python content."""
    results = []
    for kind, pattern in python_patterns():
        for m in pattern.finditer(content):
            name = m.group(1)
            if not name or name.startswith('_'):
                # Skip private-ish dunder methods but keep __init__ etc
                if name in ('__init__', '__str__', '__repr__', '__call__',
                            '__enter__', '__exit__', '__iter__', '__next__',
                            '__getitem__', '__setitem__'):
                    pass
                elif name.startswith('__') and name.endswith('__'):
                    continue
            if len(name) <= 1:
                continue
            line = content[:m.start()].count('\n') + 1
            results.append((name, kind, line))
    return results


def extract_go(content: str) -> list[tuple[str, str, int]]:
    """Extract (name, kind, line) from Go content."""
    results = []
    for kind, pattern in go_patterns():
        for m in pattern.finditer(content):
            name = m.group(1) or m.group(2)  # group 2 is the func name (group 1 = receiver)
            if not name:
                continue
            if name in ('if', 'for', 'range', 'switch', 'select', 'go', 'defer',
                        'return', 'func', 'type', 'var', 'const', 'import', 'package',
                        'map', 'chan', 'struct', 'interface'):
                continue
            if len(name) <= 1:
                continue
            line = content[:m.start()].count('\n') + 1
            results.append((name, kind, line))
    return results


def extract_vue(content: str) -> list[tuple[str, str, int]]:
    """Extract (name, kind, line) from Vue SFC — <script> block."""
    script_match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
    if not script_match:
        return []
    script_content = script_match.group(1)
    # The line numbers need offset for the script block position
    offset = content[:script_match.start()].count('\n')
    results = extract_js_ts(script_content)
    # Adjust line numbers
    return [(name, kind, line + offset) for name, kind, line in results]


# ── Call Extraction ───────────────────────────────────────────

CALL_RE = re.compile(r'(?:^|[^\w.])([a-zA-Z_]\w{1,40})\s*\(', re.MULTILINE)


def extract_calls(content: str, filepath: str, known_names: set[str]) -> list[tuple[str, str, int, str]]:
    """Extract (callee_name, caller_context, line, file) from content.
    Only returns calls where callee_name matches a known symbol name.
    """
    results = []
    for m in CALL_RE.finditer(content):
        name = m.group(1)
        if name in known_names:
            line = content[:m.start()].count('\n') + 1
            results.append((name, '', line, filepath))
    return results


# ── SQLite Setup ──────────────────────────────────────────────

def get_db_path(project_path: str) -> str:
    index_dir = os.path.join(project_path, INDEX_DIR)
    return os.path.join(index_dir, DB_NAME)


def setup_db(db_path: str) -> sqlite3.Connection:
    """Create/upgrade schema and return connection."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            language TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id INTEGER NOT NULL,
            callee_name TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            FOREIGN KEY (caller_id) REFERENCES symbols(id)
        );
        CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
        CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
        CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_name);
        CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts
            USING fts5(name, kind, file, content=symbols, content_rowid=id);
    """)
    conn.commit()
    return conn


def rebuild_fts(conn: sqlite3.Connection):
    """Rebuild FTS index from symbols table."""
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.commit()


# ── Indexing ──────────────────────────────────────────────────

def index_project(project_path: str):
    """Full index of a project. Creates/overwrites the database."""
    db_path = get_db_path(project_path)
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    # Remove old DB to start fresh
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = setup_db(db_path)

    symbols = []  # [(name, kind, file, line, language)]
    file_symbols = {}  # file -> [(name, kind, line)]
    files_scanned = 0

    root = Path(project_path)
    for filepath in root.rglob('*'):
        if filepath.is_dir():
            if any(p in SKIP_DIRS for p in filepath.parts):
                continue
            continue
        if should_skip(filepath):
            continue

        lang = classify_language(str(filepath))
        if not lang:
            continue

        try:
            content = filepath.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        relpath = str(filepath.relative_to(root))

        if lang == 'vue':
            extracted = extract_vue(content)
        elif lang in ('javascript', 'typescript'):
            extracted = extract_js_ts(content)
        elif lang == 'python':
            extracted = extract_python(content)
        elif lang == 'go':
            extracted = extract_go(content)
        else:
            continue  # wxml, swan — no symbol extraction yet

        files_scanned += 1
        file_syms = []
        for name, kind, line in extracted:
            symbols.append((name, kind, relpath, line, lang))
            file_syms.append((name, kind, line))
        if file_syms:
            file_symbols[relpath] = file_syms

    # Insert symbols
    conn.executemany(
        "INSERT INTO symbols(name, kind, file, line, language) VALUES(?, ?, ?, ?, ?)",
        symbols
    )
    conn.commit()

    # Build set of known symbol names for call extraction
    known_names = {s[0] for s in symbols}

    # Extract calls — scan files again for call relationships
    for filepath, syms in file_symbols.items():
        fullpath = os.path.join(project_path, filepath)
        try:
            content = Path(fullpath).read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        calls_found = extract_calls(content, filepath, known_names)
        # For each call, find the caller (the symbol whose body contains this call)
        for callee_name, _, line, fpath in calls_found:
            # Find the "caller" — the immediately enclosing symbol in this file
            enclosing_symbol = None
            for sname, skind, sline in syms:
                if sline <= line:
                    enclosing_symbol = sname
            if enclosing_symbol:
                # Get caller_id
                cur = conn.execute(
                    "SELECT id FROM symbols WHERE name=? AND file=?",
                    (enclosing_symbol, filepath)
                )
                row = cur.fetchone()
                if row:
                    conn.execute(
                        "INSERT INTO calls(caller_id, callee_name, file, line) VALUES(?, ?, ?, ?)",
                        (row[0], callee_name, filepath, line)
                    )

    conn.commit()

    # Build FTS
    rebuild_fts(conn)

    # Store metadata
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        ('indexed_at', datetime.now().isoformat())
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        ('files_scanned', str(files_scanned))
    )
    conn.commit()

    symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    call_count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    conn.close()

    return {
        'files_scanned': files_scanned,
        'symbols_indexed': symbol_count,
        'calls_indexed': call_count,
        'db_path': db_path,
    }


# ── MCP Server ────────────────────────────────────────────────

mcp = FastMCP("code-indexer")


def _conn():
    """Get a connection to the project's SQLite database."""
    db_path = get_db_path(os.getcwd())
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool(
    name="code_index_search",
    description="Search for a symbol by name. Returns matching symbols with file path and line number."
)
def code_index_search(name: str, kind: str = "") -> list[dict]:
    """Search for symbols matching `name`. Optionally filter by kind (function/class/variable etc)."""
    conn = _conn()
    if conn is None:
        return [{'error': 'No index found. Run code_index_init first.'}]
    try:
        if kind:
            rows = conn.execute(
                "SELECT name, kind, file, line, language FROM symbols "
                "WHERE name LIKE ? AND kind = ? ORDER BY name LIMIT 50",
                (f'%{name}%', kind)
            ).fetchall()
        else:
            # Strategy: FTS prefix match -> FTS exact -> LIKE substring
            rows = []
            # FTS prefix match: name*
            try:
                rows = conn.execute(
                    "SELECT s.name, s.kind, s.file, s.line, s.language "
                    "FROM symbols_fts f JOIN symbols s ON f.rowid = s.id "
                    "WHERE symbols_fts MATCH ? ORDER BY rank LIMIT 50",
                    (f'{name}*',)
                ).fetchall()
            except Exception:
                pass
            if not rows:
                # FTS exact match
                try:
                    rows = conn.execute(
                        "SELECT s.name, s.kind, s.file, s.line, s.language "
                        "FROM symbols_fts f JOIN symbols s ON f.rowid = s.id "
                        "WHERE symbols_fts MATCH ? ORDER BY rank LIMIT 50",
                        (name,)
                    ).fetchall()
                except Exception:
                    pass
            if not rows:
                # LIKE substring fallback
                rows = conn.execute(
                    "SELECT name, kind, file, line, language FROM symbols "
                    "WHERE name LIKE ? ORDER BY name LIMIT 50",
                    (f'%{name}%',)
                ).fetchall()
            if not rows:
                # Case-insensitive LIKE as last resort
                rows = conn.execute(
                    "SELECT name, kind, file, line, language FROM symbols "
                    "WHERE name LIKE ? ORDER BY name LIMIT 50",
                    (f'%{name.lower()}%',)
                ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool(
    name="code_index_callers",
    description="Find all callers (who calls) a given symbol. Returns list of caller symbols."
)
def code_index_callers(name: str) -> list[dict]:
    """Find all symbols that CALL `name`."""
    conn = _conn()
    if conn is None:
        return [{'error': 'No index found. Run code_index_init first.'}]
    try:
        rows = conn.execute(
            "SELECT DISTINCT s.name, s.kind, s.file, s.line, s.language "
            "FROM calls c JOIN symbols s ON c.caller_id = s.id "
            "WHERE c.callee_name = ? LIMIT 50",
            (name,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool(
    name="code_index_callees",
    description="Find all symbols called BY a given symbol. Returns list of callee symbols."
)
def code_index_callees(name: str) -> list[dict]:
    """Find all symbols that `name` calls."""
    conn = _conn()
    if conn is None:
        return [{'error': 'No index found. Run code_index_init first.'}]
    try:
        rows = conn.execute(
            "SELECT DISTINCT c.callee_name, c.file, c.line "
            "FROM calls c JOIN symbols s ON c.caller_id = s.id "
            "WHERE s.name = ? LIMIT 50",
            (name,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool(
    name="code_index_impact",
    description="Analyze the impact radius of a symbol. Returns both upstream callers and downstream callees."
)
def code_index_impact(name: str) -> dict:
    """Full impact analysis: who calls this, and what does this call."""
    conn = _conn()
    if conn is None:
        return {'error': 'No index found. Run code_index_init first.'}
    try:
        # First find the symbol itself
        sym = conn.execute(
            "SELECT name, kind, file, line, language FROM symbols WHERE name = ? LIMIT 1",
            (name,)
        ).fetchone()
        if not sym:
            return {'error': f'Symbol "{name}" not found in index.'}

        callers = conn.execute(
            "SELECT DISTINCT s.name, s.kind, s.file, s.line "
            "FROM calls c JOIN symbols s ON c.caller_id = s.id "
            "WHERE c.callee_name = ? LIMIT 30",
            (name,)
        ).fetchall()

        callees = conn.execute(
            "SELECT DISTINCT c.callee_name, c.file, c.line "
            "FROM calls c JOIN symbols s ON c.caller_id = s.id "
            "WHERE s.name = ? LIMIT 30",
            (name,)
        ).fetchall()

        return {
            'symbol': dict(sym),
            'callers': [dict(r) for r in callers],
            'callees': [dict(r) for r in callees],
        }
    finally:
        conn.close()


@mcp.tool(
    name="code_index_init",
    description="Initialize or rebuild the code index for the current project. Run this first."
)
def code_index_init(path: str = "") -> dict:
    """Build/re-build the code index. Uses current directory unless path is provided."""
    project_path = path if path else os.getcwd()
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        return {'error': f'Not a directory: {project_path}'}
    return index_project(project_path)


@mcp.tool(
    name="code_index_stats",
    description="Get statistics about the current code index."
)
def code_index_stats() -> dict:
    """Return index statistics."""
    conn = _conn()
    if conn is None:
        return {'error': 'No index found. Run code_index_init first.'}
    try:
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        call_count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        file_count = conn.execute(
            "SELECT COUNT(DISTINCT file) FROM symbols"
        ).fetchone()[0]
        lang_breakdown = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM symbols GROUP BY language ORDER BY cnt DESC"
        ).fetchall()
        indexed_at = conn.execute(
            "SELECT value FROM meta WHERE key='indexed_at'"
        ).fetchone()
        return {
            'symbols': symbol_count,
            'calls': call_count,
            'files': file_count,
            'languages': {r['language']: r['cnt'] for r in lang_breakdown},
            'indexed_at': indexed_at['value'] if indexed_at else 'unknown',
        }
    finally:
        conn.close()


# ── Entry Point ───────────────────────────────────────────────

def main():
    """Entry point for the MCP server. Run via `code-indexer` command after pip install."""
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
