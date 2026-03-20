# wwdc-docs-mcp

An MCP server that gives [Claude Code](https://claude.ai/code) accurate, up-to-date Apple developer documentation — WWDC session transcripts, code samples, and Apple SDK symbol references — without waiting for a model to be retrained.

## The Problem

Apple ships new frameworks and APIs every June at WWDC. Language models take months to catch up. In the meantime, asking Claude about `AlarmKit`, `NavigationSplitView` availability on iOS 16, or any other recently introduced API often returns outdated or hallucinated answers.

## How It Works

This tool does two things:

1. **Scrapes WWDC session pages** — transcripts, chapter summaries, and code snippets are pulled directly from `developer.apple.com` and stored locally as flat files.
2. **Queries Apple's documentation JSON API** — symbol declarations, availability (`introducedAt`), and full documentation prose are fetched on-demand and cached.

Both data sources are exposed to Claude Code via a local [MCP server](https://modelcontextprotocol.io). Claude calls the tools automatically when it needs them — no prompt engineering required.

## Requirements

- **Python 3.13+** — `brew install python@3.13`
- **[Claude Code](https://claude.ai/code)** — must be installed and authenticated

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/wwdc-scraper.git
cd wwdc-scraper

make setup       # creates venv, installs deps, registers MCP server
make scrape      # scrapes all WWDC sessions for the current year (~2 min)
make index       # indexes SwiftUI + UIKit symbol docs
```

That's it. Open any Claude Code session and start asking about Apple APIs.

## Usage

Once set up, Claude automatically calls the MCP tools when relevant. You can also prompt it directly:

```
"What did Apple introduce for alarm functionality at WWDC 2025?"
"Show me how to schedule an AlarmKit alarm with a countdown."
"What's the full API for NavigationSplitView? What iOS version does it require?"
"Find all WWDC 2025 sessions about Swift concurrency."
```

### Available Tools

| Tool | Description |
|---|---|
| `search_wwdc` | Search session titles, descriptions, and chapter summaries by keyword |
| `get_session` | Full session details — chapters, summaries, all code snippets, optional transcript |
| `get_apple_symbol` | Symbol declaration, availability, members, and documentation prose |
| `search_symbols` | Keyword search across an indexed framework's full symbol list |

### Example Interactions

**Finding sessions on a topic:**
> Claude calls `search_wwdc("concurrency")` → returns 4 matching WWDC 2025 sessions with chapter breakdowns

**Getting code from a session:**
> Claude calls `get_session("268")` → returns all 26 Swift concurrency code snippets with video timestamps

**Checking API availability:**
> Claude calls `get_apple_symbol("swiftui", "navigationsplitview")` → returns `iOS 16.0, iPadOS 16.0, macOS 13.0 ...` with full initializer list

## Annual Update (Every June)

After WWDC videos are published:

```bash
make check              # verify scraper selectors still work (run this first)
make update             # scrape new sessions + refresh symbol indexes
```

The scraper is resume-safe — re-running it skips sessions already scraped.

```bash
make scrape YEAR=2024   # backfill a previous year
make scrape YEAR=2026   # explicit year
```

## Indexing More Frameworks

By default, `make index` indexes SwiftUI and UIKit. Add more as needed:

```bash
make index FRAMEWORKS="swiftui uikit mapkit alarmkit visionos"
```

Individual symbols are fetched on-demand and cached automatically — you don't need to index a framework before looking up a specific symbol.

## Setting Up on Another Machine

```bash
git clone https://github.com/YOUR_USERNAME/wwdc-scraper.git
cd wwdc-scraper
make setup
make scrape
make index
```

The scraped session data and symbol cache are machine-local (gitignored). Each machine builds its own from scratch, which takes about 2 minutes for a full WWDC year.

## Project Structure

```
wwdc-scraper/
├── mcp_server.py     # MCP server — exposes 4 tools to Claude Code
├── scrape.py         # WWDC session scraper (transcripts, code, summaries)
├── fetch_docs.py     # Apple symbol docs fetcher + framework indexer
├── wwdc_year.py      # Smart year resolution based on WWDC calendar
├── Makefile          # setup, scrape, index, check, update
├── requirements.txt
└── output/           # gitignored — generated locally
    ├── 2025/
    │   ├── 230/
    │   │   ├── metadata.json
    │   │   ├── transcript.md
    │   │   └── snippets/
    │   └── .../
    └── symbols/
        ├── swiftui/
        │   ├── index.json        # 1,565 symbols with declarations + abstracts
        │   └── navigationsplitview.json  # cached on first lookup
        └── .../
```

## How Session Data Is Extracted

WWDC session pages on `developer.apple.com` embed their full content in the HTML — no JavaScript rendering required. The scraper extracts:

- **Transcript** — full verbatim text from `#transcript-content`
- **Code snippets** — formatted Swift blocks with video timestamps
- **Chapter summaries** — Apple-written prose descriptions of each section
- **Chapter list** — timestamps and titles

Scraper health is validated automatically — sessions with unexpected empty transcripts emit warnings at the end of a batch run.

## How Symbol Docs Are Fetched

Apple exposes a public JSON API used by their documentation website:

```
https://developer.apple.com/tutorials/data/documentation/{framework}/{symbol}.json
```

This returns structured data including declarations, platform availability, member lists, and documentation prose. Symbols are fetched on-demand and cached locally — the first lookup hits Apple's servers, subsequent lookups are instant.

## Configuration

The MCP server is registered at user scope, so it's available in all your Claude Code projects. The data path is set via environment variable at registration time:

```bash
# Registered automatically by `make setup` — for reference:
claude mcp add --scope user wwdc-docs \
  /path/to/.venv/bin/python \
  /path/to/mcp_server.py \
  --env WWDC_DOCS_PATH=/path/to/output
```

To check server status:
```bash
make status
```

## Smart Year Defaults

The MCP server and scraper automatically resolve which WWDC year to default to:

- **Before June 15** — defaults to the previous year (current year's WWDC hasn't happened yet)
- **After June 15** — defaults to the current year, with a reminder to run `make scrape` if data isn't present yet
- **Always** — falls back to the most recent year with local data

The June 15 cutoff gives a two-week buffer past the earliest WWDC has ever been scheduled.

## Contributing

Issues and pull requests welcome. If Apple changes the HTML structure of their session pages and the scraper breaks, the fix is usually a one-line selector update in `scrape.py` — the `make check` command will tell you immediately.

When contributing:
- Run `make check` before submitting a scraper change
- The four MCP tool descriptions (docstrings in `mcp_server.py`) are what Claude reads to decide when to call each tool — keep them accurate and specific

## License

MIT
