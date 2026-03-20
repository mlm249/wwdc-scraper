PYTHON     := .venv/bin/python
PIP        := .venv/bin/pip
YEAR       ?= $(shell $(PYTHON) -c "from wwdc_year import current_wwdc_year; print(current_wwdc_year())" 2>/dev/null || date +%Y)
FRAMEWORKS ?= swiftui uikit
PYTHONPATH := $(shell pwd)/src

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: setup
setup: .venv register
	@echo ""
	@echo "Done. wwdc-docs MCP server is ready."

.venv:
	python3.13 -m venv .venv
	$(PIP) install --quiet -r requirements.txt

.PHONY: register
register: .venv
	claude mcp add --scope user wwdc-docs \
		$(shell pwd)/.venv/bin/python \
		$(shell pwd)/src/mcp_server.py \
		--env WWDC_DOCS_PATH=$(shell pwd)/output 2>/dev/null || \
	claude mcp add --scope user wwdc-docs \
		$(shell pwd)/.venv/bin/python \
		$(shell pwd)/src/mcp_server.py \
		--env WWDC_DOCS_PATH=$(shell pwd)/output

# ── Scraping ──────────────────────────────────────────────────────────────────

.PHONY: scrape
scrape: .venv
	$(PYTHON) src/scrape.py --all $(YEAR)

.PHONY: index
index: .venv
	$(PYTHON) src/fetch_docs.py $(FRAMEWORKS)

# ── Aliases ───────────────────────────────────────────────────────────────────

.PHONY: update
update: scrape index
	@echo "WWDC $(YEAR) sessions and symbol indexes updated."

.PHONY: check
check: .venv
	@echo "Health check: scraping one session to verify selectors still work..."
	@PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
import sys; \
from scrape import scrape_session, validate_session; \
r = scrape_session('https://developer.apple.com/videos/play/wwdc$(YEAR)/230/'); \
w = validate_session(r); \
print(f\"  transcript: {r['transcript_paragraph_count']} paragraphs\"); \
print(f\"  code snippets: {r['code_snippet_count']}\"); \
print(f\"  chapter summaries: {len(r['chapter_summaries'])}\"); \
[print(f'  WARNING: ' + x) for x in w]; \
sys.exit(1 if w else 0) \
"
	@echo "Check passed — scraper selectors are working."

.PHONY: status
status:
	claude mcp get wwdc-docs

.PHONY: help
help:
	@echo "Usage:"
	@echo "  make setup                       Install deps + register MCP server (first-time)"
	@echo "  make scrape                      Scrape WWDC sessions (default: current year)"
	@echo "  make scrape YEAR=2024            Scrape a specific year"
	@echo "  make index                       Index symbol docs (default: swiftui uikit)"
	@echo "  make index FRAMEWORKS=\"swiftui alarmkit mapkit\""
	@echo "  make update                      scrape + index in one shot"
	@echo "  make check                       Verify scraper selectors still work"
	@echo "  make status                      Show MCP server connection status"
