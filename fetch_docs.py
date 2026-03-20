#!/usr/bin/env python3
"""
Apple developer documentation fetcher.

Usage:
  python3 fetch_docs.py <framework> [<framework> ...]   # index one or more frameworks
  python3 fetch_docs.py --symbol <framework> <symbol>   # fetch + cache a single symbol
  python3 fetch_docs.py --deep <framework>              # index + fetch all symbols

Examples:
  python3 fetch_docs.py swiftui alarmkit uikit
  python3 fetch_docs.py --symbol swiftui navigationsplitview
  python3 fetch_docs.py --deep alarmkit
"""

import sys
import json
import time
import requests
from pathlib import Path

DOCS_BASE = "https://developer.apple.com/tutorials/data"
DEFAULT_DELAY = 0.5
OUTPUT_ROOT = Path("output/symbols")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_json(path: str) -> dict:
    """Fetch a /documentation/... path as JSON."""
    url = DOCS_BASE + path + ".json"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_abstract(abstract_list: list) -> str:
    """Flatten Apple's [{type, text}] abstract format to a plain string."""
    return "".join(
        item.get("text", "") for item in (abstract_list or [])
        if item.get("type") in ("text", "codeVoice")
    ).strip()


def extract_declaration(fragments: list) -> str:
    """Reconstruct a Swift declaration from token fragments, e.g. 'class AlarmManager'."""
    return "".join(f.get("text", "") for f in (fragments or [])).strip()


def extract_platforms(platforms: list) -> list[dict]:
    """Condense platform availability to just the fields we care about."""
    result = []
    for p in (platforms or []):
        entry = {"name": p["name"]}
        if "introducedAt" in p:
            entry["introducedAt"] = p["introducedAt"]
        if p.get("deprecated"):
            entry["deprecated"] = True
        if p.get("beta"):
            entry["beta"] = True
        if p.get("unavailable"):
            entry["unavailable"] = True
        result.append(entry)
    return result


def extract_doc_text(primary_content_sections: list) -> str:
    """Extract prose documentation from primaryContentSections[kind=content]."""
    lines = []
    for section in (primary_content_sections or []):
        if section.get("kind") != "content":
            continue
        for block in section.get("content", []):
            block_type = block.get("type")
            if block_type == "paragraph":
                text = extract_abstract(block.get("inlineContent", []))
                if text:
                    lines.append(text)
            elif block_type == "heading":
                lines.append(f"\n## {block.get('text', '')}")
            elif block_type == "codeListing":
                code = "\n".join(block.get("code", []))
                if code:
                    lines.append(f"```swift\n{code}\n```")
            elif block_type == "unorderedList":
                for item in block.get("items", []):
                    for content in item.get("content", []):
                        text = extract_abstract(content.get("inlineContent", []))
                        if text:
                            lines.append(f"- {text}")
    return "\n\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Framework index
# ---------------------------------------------------------------------------

def _collect_symbols_from_refs(refs: dict, framework: str) -> list[dict]:
    """Extract symbol entries from a references dict."""
    symbols = []
    for ref in refs.values():
        role = ref.get("role")
        if role not in ("symbol",):
            continue
        ref_id = ref.get("identifier", "")
        if framework.lower() not in ref_id.lower():
            continue
        title = ref.get("title", "")
        if not title:
            continue
        symbols.append({
            "title": title,
            "abstract": extract_abstract(ref.get("abstract", [])),
            "declaration": extract_declaration(ref.get("fragments", [])),
            "url": ref.get("url", ""),
            "kind": ref.get("kind", ""),
        })
    return symbols


def index_framework(framework: str, delay: float = DEFAULT_DELAY) -> dict:
    """
    Fetch the framework-level JSON and build a condensed symbol index.
    For large frameworks (e.g. SwiftUI), walks category pages one level deep.
    Returns a dict with framework metadata + list of all symbols.
    """
    path = f"/documentation/{framework}"
    print(f"  Fetching {DOCS_BASE}{path}.json ...")
    data = fetch_json(path)

    meta = data.get("metadata", {})
    index = {
        "framework": framework,
        "title": meta.get("title", framework),
        "abstract": extract_abstract(data.get("abstract", [])),
        "platforms": extract_platforms(meta.get("platforms", [])),
        "symbols": [],
    }

    refs = data.get("references", {})
    seen_urls = set()

    # Collect any directly listed symbols
    for sym in _collect_symbols_from_refs(refs, framework):
        if sym["url"] not in seen_urls:
            seen_urls.add(sym["url"])
            index["symbols"].append(sym)

    # Walk collectionGroup category pages one level deep to find remaining symbols
    category_refs = [
        r for r in refs.values()
        if r.get("role") == "collectionGroup" and framework.lower() in r.get("identifier", "").lower()
    ]
    if category_refs:
        print(f"  Walking {len(category_refs)} category pages...")
    for cat in category_refs:
        cat_url = cat.get("url", "")
        if not cat_url:
            continue
        try:
            cat_data = fetch_json(cat_url)
            cat_refs = cat_data.get("references", {})
            for sym in _collect_symbols_from_refs(cat_refs, framework):
                if sym["url"] not in seen_urls:
                    seen_urls.add(sym["url"])
                    index["symbols"].append(sym)
            time.sleep(delay)
        except Exception as e:
            print(f"    ✗ {cat_url}: {e}")

    index["symbols"].sort(key=lambda s: s["title"])
    print(f"  Found {len(index['symbols'])} symbols.")
    return index


def save_framework_index(framework: str, index: dict) -> Path:
    out_dir = OUTPUT_ROOT / framework
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.json"
    path.write_text(json.dumps(index, indent=2))
    return path


# ---------------------------------------------------------------------------
# Individual symbol
# ---------------------------------------------------------------------------

def fetch_symbol(framework: str, symbol_url_path: str) -> dict:
    """
    Fetch a single symbol's full documentation and return a condensed dict.
    symbol_url_path is the `url` field from references, e.g. '/documentation/swiftui/text'
    """
    data = fetch_json(symbol_url_path)
    meta = data.get("metadata", {})

    # Collect member names from topicSections
    refs = data.get("references", {})
    members = []
    for section in data.get("topicSections", []):
        for ident in section.get("identifiers", []):
            ref = refs.get(ident, {})
            if ref.get("title"):
                members.append({
                    "group": section.get("title", ""),
                    "title": ref["title"],
                    "declaration": extract_declaration(ref.get("fragments", [])),
                    "abstract": extract_abstract(ref.get("abstract", [])),
                    "url": ref.get("url", ""),
                })

    return {
        "title": meta.get("title", ""),
        "symbolKind": meta.get("symbolKind", ""),
        "roleHeading": meta.get("roleHeading", ""),
        "declaration": extract_declaration(meta.get("fragments", [])),
        "abstract": extract_abstract(data.get("abstract", [])),
        "platforms": extract_platforms(meta.get("platforms", [])),
        "documentation": extract_doc_text(data.get("primaryContentSections", [])),
        "members": members,
        "url": symbol_url_path,
    }


def cache_symbol(framework: str, symbol_path: str, data: dict) -> Path:
    # symbol_path like '/documentation/swiftui/text' → swiftui/text.json
    parts = symbol_path.strip("/").split("/")  # ['documentation', 'swiftui', 'text']
    symbol_name = parts[-1]
    out_dir = OUTPUT_ROOT / framework
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol_name}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def get_symbol(framework: str, symbol_name: str) -> dict:
    """Fetch-or-cache a symbol. symbol_name is the lowercase symbol path component."""
    cached = OUTPUT_ROOT / framework / f"{symbol_name}.json"
    if cached.exists():
        print(f"  Cache hit: {cached}")
        return json.loads(cached.read_text())

    path = f"/documentation/{framework}/{symbol_name}"
    print(f"  Fetching {DOCS_BASE}{path}.json ...")
    data = fetch_symbol(framework, path)
    out = cache_symbol(framework, path, data)
    print(f"  Cached to {out}")
    return data


def print_symbol(data: dict):
    print(f"\n{'='*55}")
    print(f"{data['roleHeading']}: {data['declaration']}")
    print(f"{'='*55}")
    if data["abstract"]:
        print(f"\n{data['abstract']}\n")
    if data["platforms"]:
        avail = ", ".join(
            f"{p['name']} {p.get('introducedAt', '?')}{'β' if p.get('beta') else ''}"
            for p in data["platforms"]
        )
        print(f"Availability: {avail}\n")
    if data["documentation"]:
        print(f"Documentation:\n{data['documentation'][:800]}")
    if data["members"]:
        print(f"\nMembers ({len(data['members'])}):")
        current_group = None
        for m in data["members"][:20]:
            if m["group"] != current_group:
                current_group = m["group"]
                print(f"  [{current_group}]")
            print(f"    {m['declaration'] or m['title']}")
            if m["abstract"]:
                print(f"      → {m['abstract'][:80]}")
        if len(data["members"]) > 20:
            print(f"  ... and {len(data['members']) - 20} more")


# ---------------------------------------------------------------------------
# Deep fetch (all symbols in a framework)
# ---------------------------------------------------------------------------

def deep_fetch(framework: str, delay: float = DEFAULT_DELAY):
    index_path = OUTPUT_ROOT / framework / "index.json"
    if not index_path.exists():
        print(f"No index for {framework}, indexing first...")
        index = index_framework(framework)
        save_framework_index(framework, index)
    else:
        index = json.loads(index_path.read_text())

    symbols = [s for s in index["symbols"] if s["url"] and s["role"] == "symbol"]
    print(f"\nFetching {len(symbols)} symbols for {framework}...\n")

    for i, sym in enumerate(symbols, 1):
        sym_name = sym["url"].strip("/").split("/")[-1]
        cached = OUTPUT_ROOT / framework / f"{sym_name}.json"
        if cached.exists():
            print(f"  [{i}/{len(symbols)}] skip  {sym['title']}")
            continue
        print(f"  [{i}/{len(symbols)}] fetch {sym['title']}", end="", flush=True)
        try:
            data = fetch_symbol(framework, sym["url"])
            cache_symbol(framework, sym["url"], data)
            print(f"  ✓")
        except Exception as e:
            print(f"  ✗  {e}")
        if i < len(symbols):
            time.sleep(delay)

    print(f"\nDone.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--symbol":
        if len(args) < 3:
            print("Usage: --symbol <framework> <symbol>")
            sys.exit(1)
        framework, symbol = args[1].lower(), args[2].lower()
        data = get_symbol(framework, symbol)
        print_symbol(data)

    elif args[0] == "--deep":
        if len(args) < 2:
            print("Usage: --deep <framework>")
            sys.exit(1)
        framework = args[1].lower()
        print(f"Deep fetching {framework}...")
        index = index_framework(framework)
        save_framework_index(framework, index)
        deep_fetch(framework)

    else:
        # Index one or more frameworks
        for framework in args:
            framework = framework.lower()
            print(f"\nIndexing {framework}...")
            try:
                index = index_framework(framework)
                path = save_framework_index(framework, index)
                print(f"  Saved to {path}")
                print(f"  Platform availability: {index['platforms']}")
                print(f"  Sample symbols: {[s['title'] for s in index['symbols'][:5]]}")
            except Exception as e:
                print(f"  ✗ {e}")


if __name__ == "__main__":
    main()
