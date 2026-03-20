#!/usr/bin/env python3
"""
WWDC Docs MCP Server

Exposes Apple WWDC session transcripts, code snippets, and symbol docs
as tools Claude can call on demand.

Configuration via environment variables:
  WWDC_DOCS_PATH   Path to the output/ directory (default: ./output)
"""

import json
import os
import re
import time
import requests
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from wwdc_year import resolve_year, current_wwdc_year

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.environ.get("WWDC_DOCS_PATH", Path(__file__).parent / "output"))
SESSIONS_ROOT = DATA_ROOT / "sessions" if (DATA_ROOT / "sessions").exists() else DATA_ROOT
SYMBOLS_ROOT = DATA_ROOT / "symbols"
DOCS_BASE = "https://developer.apple.com/tutorials/data"

mcp = FastMCP("wwdc-docs")

# Resolve the best default year at startup
_DEFAULT_YEAR, _YEAR_WARNING = resolve_year(DATA_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _abstract_text(abstract_list: list) -> str:
    return "".join(
        item.get("text", "") for item in (abstract_list or [])
        if item.get("type") in ("text", "codeVoice")
    ).strip()


def _declaration(fragments: list) -> str:
    return "".join(f.get("text", "") for f in (fragments or [])).strip()


def _keyword_match(query: str, *fields: str) -> bool:
    q = query.lower()
    terms = q.split()
    combined = " ".join(f.lower() for f in fields if f)
    return all(t in combined for t in terms)


def _fetch_symbol_json(framework: str, symbol_url_path: str) -> dict:
    """Fetch a symbol from Apple's docs API and cache it locally."""
    url = DOCS_BASE + symbol_url_path + ".json"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    meta = data.get("metadata", {})

    def _platforms(platforms):
        result = []
        for p in (platforms or []):
            entry = {"name": p["name"]}
            if "introducedAt" in p:
                entry["introducedAt"] = p["introducedAt"]
            if p.get("beta"):
                entry["beta"] = True
            if p.get("deprecated"):
                entry["deprecated"] = True
            result.append(entry)
        return result

    def _doc_text(sections):
        lines = []
        for section in (sections or []):
            if section.get("kind") != "content":
                continue
            for block in section.get("content", []):
                t = block.get("type")
                if t == "paragraph":
                    text = _abstract_text(block.get("inlineContent", []))
                    if text:
                        lines.append(text)
                elif t == "heading":
                    lines.append(f"\n## {block.get('text', '')}")
                elif t == "codeListing":
                    code = "\n".join(block.get("code", []))
                    if code:
                        lines.append(f"```swift\n{code}\n```")
                elif t == "unorderedList":
                    for item in block.get("items", []):
                        for c in item.get("content", []):
                            text = _abstract_text(c.get("inlineContent", []))
                            if text:
                                lines.append(f"- {text}")
        return "\n\n".join(lines).strip()

    refs = data.get("references", {})
    members = []
    for section in data.get("topicSections", []):
        for ident in section.get("identifiers", []):
            ref = refs.get(ident, {})
            if ref.get("title"):
                members.append({
                    "group": section.get("title", ""),
                    "title": ref["title"],
                    "declaration": _declaration(ref.get("fragments", [])),
                    "abstract": _abstract_text(ref.get("abstract", [])),
                })

    result = {
        "title": meta.get("title", ""),
        "symbolKind": meta.get("symbolKind", ""),
        "roleHeading": meta.get("roleHeading", ""),
        "declaration": _declaration(meta.get("fragments", [])),
        "abstract": _abstract_text(data.get("abstract", [])),
        "platforms": _platforms(meta.get("platforms", [])),
        "documentation": _doc_text(data.get("primaryContentSections", [])),
        "members": members,
    }

    # Cache it
    sym_name = symbol_url_path.strip("/").split("/")[-1]
    out_dir = SYMBOLS_ROOT / framework
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{sym_name}.json").write_text(json.dumps(result, indent=2))

    return result


# ---------------------------------------------------------------------------
# Tool: search_wwdc
# ---------------------------------------------------------------------------

@mcp.tool()
def search_wwdc(query: str, year: str = "") -> str:
    """
    Search WWDC session titles, descriptions, and chapter summaries by keyword.
    Returns matching sessions with their key details. Use this when you need to
    find relevant WWDC sessions for a given topic or API.

    Args:
        query: Keywords to search for (e.g. "SwiftUI navigation", "AlarmKit", "concurrency")
        year:  WWDC year to search. Defaults to the most recent available year.
    """
    year = year or _DEFAULT_YEAR
    prefix = f"> Note: {_YEAR_WARNING}\n\n" if _YEAR_WARNING else ""

    year_dir = SESSIONS_ROOT / year
    if not year_dir.exists():
        return f"{prefix}No session data found for WWDC {year}. Run: make scrape YEAR={year}"

    results = []
    for session_dir in sorted(year_dir.iterdir(), key=lambda p: p.name):
        meta_path = session_dir / "metadata.json"
        if not meta_path.exists():
            continue
        meta = _read_json(meta_path)
        if not meta:
            continue

        searchable = " ".join([
            meta.get("title", ""),
            meta.get("description", ""),
            " ".join(meta.get("chapter_summaries", [])),
            " ".join(meta.get("chapters", [])),
        ])

        if _keyword_match(query, searchable):
            results.append({
                "session_id": meta.get("session_id"),
                "title": meta.get("title"),
                "description": meta.get("description", "")[:200],
                "chapters": meta.get("chapters", []),
                "snippet_count": meta.get("code_snippet_count", 0),
                "has_transcript": bool((session_dir / "transcript.md").exists()),
            })

    if not results:
        return f"{prefix}No WWDC {year} sessions found matching '{query}'."

    lines = [f"{prefix}Found {len(results)} WWDC {year} session(s) matching '{query}':\n"]
    for r in results:
        lines.append(f"## {r['title']} (Session {r['session_id']})")
        lines.append(f"{r['description']}")
        if r["chapters"]:
            lines.append(f"Chapters: {', '.join(r['chapters'])}")
        lines.append(f"Code snippets: {r['snippet_count']}  |  Transcript: {'yes' if r['has_transcript'] else 'no'}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_session
# ---------------------------------------------------------------------------

@mcp.tool()
def get_session(session_id: str, year: str = "", include_transcript: bool = False) -> str:
    """
    Get full details for a WWDC session: description, chapter summaries, and
    all code snippets. Optionally include the full transcript.
    Use search_wwdc first to find the session ID.

    Args:
        session_id:          Numeric session ID (e.g. "230")
        year:                WWDC year. Defaults to the most recent available year.
        include_transcript:  Whether to include the full transcript text (can be long)
    """
    year = year or _DEFAULT_YEAR
    session_dir = SESSIONS_ROOT / year / session_id
    if not session_dir.exists():
        return f"Session {session_id} not found for WWDC {year}."

    meta = _read_json(session_dir / "metadata.json")
    if not meta:
        return f"Could not read metadata for session {session_id}."

    lines = []
    lines.append(f"# {meta['title']} — WWDC{year} Session {session_id}")
    lines.append(f"\n{meta.get('description', '')}\n")

    if meta.get("chapters"):
        lines.append("## Chapters")
        for ch in meta["chapters"]:
            lines.append(f"- {ch}")
        lines.append("")

    if meta.get("chapter_summaries"):
        lines.append("## Chapter Summaries")
        for summary in meta["chapter_summaries"]:
            lines.append(f"{summary}\n")

    # Code snippets
    snippets_dir = session_dir / "snippets"
    if snippets_dir.exists():
        snippet_files = sorted(snippets_dir.glob("*.swift"))
        if snippet_files:
            lines.append(f"## Code Snippets ({len(snippet_files)} total)")
            for f in snippet_files:
                lines.append(f"\n### {f.stem}")
                lines.append(f"```swift\n{f.read_text().strip()}\n```")

    # Transcript
    transcript_path = session_dir / "transcript.md"
    if include_transcript and transcript_path.exists():
        transcript = transcript_path.read_text()
        lines.append("\n## Full Transcript")
        lines.append(transcript)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_apple_symbol
# ---------------------------------------------------------------------------

@mcp.tool()
def get_apple_symbol(framework: str, symbol: str) -> str:
    """
    Get documentation for a specific Apple SDK symbol: declaration, availability,
    description, and member list. Fetches from Apple's docs API on first use,
    then returns from local cache. Use this when you need accurate availability
    info or full API details for a Swift symbol.

    Args:
        framework:  Framework name, lowercase (e.g. "swiftui", "uikit", "alarmkit")
        symbol:     Symbol name, lowercase (e.g. "navigationsplitview", "alarmmanager")
    """
    framework = framework.lower()
    symbol = symbol.lower()

    # Check cache first
    cached_path = SYMBOLS_ROOT / framework / f"{symbol}.json"
    if cached_path.exists():
        data = _read_json(cached_path)
    else:
        try:
            data = _fetch_symbol_json(framework, f"/documentation/{framework}/{symbol}")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return (
                    f"Symbol '{symbol}' not found in {framework}. "
                    f"Check the name is lowercase and matches the URL path component "
                    f"(e.g. NavigationSplitView → navigationsplitview)."
                )
            return f"Error fetching symbol: {e}"
        except Exception as e:
            return f"Error fetching symbol: {e}"

    if not data:
        return f"No data found for {framework}/{symbol}."

    lines = []
    lines.append(f"# {data['roleHeading']}: `{data['declaration']}`\n")

    if data.get("abstract"):
        lines.append(f"{data['abstract']}\n")

    if data.get("platforms"):
        avail = ", ".join(
            f"{p['name']} {p.get('introducedAt', '?')}{'β' if p.get('beta') else ''}"
            for p in data["platforms"]
            if not p.get("unavailable")
        )
        lines.append(f"**Availability:** {avail}\n")

    if data.get("documentation"):
        lines.append(f"## Documentation\n{data['documentation']}\n")

    if data.get("members"):
        lines.append(f"## Members ({len(data['members'])})")
        current_group = None
        for m in data["members"]:
            if m["group"] != current_group:
                current_group = m["group"]
                lines.append(f"\n**{current_group}**")
            decl = m.get("declaration") or m["title"]
            abstract = f" — {m['abstract']}" if m.get("abstract") else ""
            lines.append(f"- `{decl}`{abstract[:100]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: search_symbols
# ---------------------------------------------------------------------------

@mcp.tool()
def search_symbols(framework: str, query: str) -> str:
    """
    Search the symbol index for an Apple framework by keyword. Returns matching
    symbols with their declarations and descriptions. Requires the framework
    index to have been built first via fetch_docs.py.

    Args:
        framework:  Framework name, lowercase (e.g. "swiftui", "uikit")
        query:      Keywords to search (e.g. "navigation", "animation", "list")
    """
    framework = framework.lower()
    index_path = SYMBOLS_ROOT / framework / "index.json"

    if not index_path.exists():
        return (
            f"No symbol index found for '{framework}'. "
            f"Run: python3 fetch_docs.py {framework}"
        )

    index = _read_json(index_path)
    if not index:
        return f"Could not read symbol index for '{framework}'."

    matches = [
        s for s in index.get("symbols", [])
        if _keyword_match(query, s.get("title", ""), s.get("abstract", ""), s.get("declaration", ""))
    ]

    if not matches:
        return f"No symbols found in {framework} matching '{query}'."

    lines = [f"Found {len(matches)} symbol(s) in {framework} matching '{query}':\n"]
    for s in matches[:30]:  # cap at 30 results
        decl = s.get("declaration") or s["title"]
        abstract = s.get("abstract", "")
        lines.append(f"- `{decl}`")
        if abstract:
            lines.append(f"  {abstract[:120]}")
    if len(matches) > 30:
        lines.append(f"\n...and {len(matches) - 30} more. Refine your query to narrow results.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"WWDC Docs MCP server starting...")
    print(f"Data root: {DATA_ROOT.resolve()}")
    mcp.run()
