#!/usr/bin/env python3
"""
WWDC session scraper.

Usage:
  python3 scrape.py <session-url>              # scrape single session
  python3 scrape.py --all <listing-url>        # discover + scrape all sessions
  python3 scrape.py --discover <listing-url>   # just print discovered URLs
"""

import sys
import json
import re
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path

BASE_URL = "https://developer.apple.com"
DEFAULT_DELAY = 1.0  # seconds between requests


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_sessions(listing_url: str) -> list[dict]:
    """Return list of {url, session_id, title} from a WWDC listing page."""
    print(f"Discovering sessions from {listing_url}...")
    resp = requests.get(listing_url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    sessions = []
    seen = set()

    for link in soup.find_all("a", href=lambda h: h and "/videos/play/" in h):
        href = link["href"]
        if href in seen:
            continue
        seen.add(href)

        match = re.search(r"/videos/play/wwdc(\d+)/(\d+)/", href)
        if not match:
            continue

        year = match.group(1)
        session_id = match.group(2)
        title_tag = link.find(["h3", "h5"])
        title = title_tag.get_text(strip=True) if title_tag else ""
        url = BASE_URL + href if href.startswith("/") else href

        sessions.append({"url": url, "year": year, "session_id": session_id, "title": title})

    print(f"Found {len(sessions)} sessions.")
    return sessions


# ---------------------------------------------------------------------------
# Single session scraper
# ---------------------------------------------------------------------------

def scrape_session(url: str) -> dict:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Metadata
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    title = title_tag.get("content") if title_tag and title_tag.get("content") else title_tag.get_text(strip=True)
    title = re.sub(r"\s*-\s*WWDC\d+.*$", "", title).strip()

    desc_tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    description = desc_tag.get("content", "").strip() if desc_tag else ""

    match = re.search(r"/wwdc(\d+)/(\d+)", url)
    year = match.group(1) if match else "unknown"
    session_id = match.group(2) if match else "unknown"

    # Chapters
    chapters = []
    chapter_list = soup.find("ul", class_="chapter-list")
    if chapter_list:
        for item in chapter_list.find_all("li", class_="chapter-item"):
            text = item.get_text(separator=" ", strip=True)
            if text:
                chapters.append(text)

    # Chapter summaries (Apple-written prose per section)
    chapter_summaries = []
    summary_section = soup.find("li", class_=lambda c: c and "summary" in c and "supplement" in c if c else False)
    if summary_section:
        for p in summary_section.find_all("p"):
            text = p.get_text(separator=" ", strip=True)
            if text:
                chapter_summaries.append(text)

    # Transcript
    transcript_paragraphs = []
    transcript_section = soup.find(id="transcript-content")
    if transcript_section:
        for p in transcript_section.find_all("p"):
            text = p.get_text(separator=" ", strip=True)
            if text:
                transcript_paragraphs.append(text)

    # Code snippets with timestamps
    code_snippets = []
    for container in soup.find_all("li", class_="sample-code-main-container"):
        timestamp_tag = container.find("p")
        timestamp = timestamp_tag.get_text(strip=True) if timestamp_tag else ""
        pre = container.find("pre", class_="code-source")
        if pre:
            code_tag = pre.find("code")
            if code_tag:
                code = code_tag.get_text().strip()
                if code:
                    code_snippets.append({"timestamp": timestamp, "code": code})

    return {
        "url": url,
        "year": year,
        "session_id": session_id,
        "title": title,
        "description": description,
        "chapters": chapters,
        "chapter_summaries": chapter_summaries,
        "transcript": "\n\n".join(transcript_paragraphs),
        "transcript_paragraph_count": len(transcript_paragraphs),
        "code_snippets": code_snippets,
        "code_snippet_count": len(code_snippets),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def validate_session(result: dict) -> list[str]:
    """
    Return a list of warning strings for unexpected scrape results.
    Nearly all sessions have transcripts; known exceptions are keynotes,
    ASL versions, recaps, and welcome sessions.
    """
    warnings = []
    no_transcript_ok = any(
        kw in result["title"].lower()
        for kw in ("keynote", "asl", "recap", "welcome", "state of the union")
    )
    if result["transcript_paragraph_count"] == 0 and not no_transcript_ok:
        warnings.append("no transcript found (selector may have changed)")
    if result["chapter_summaries"] == [] and result["transcript_paragraph_count"] > 30:
        warnings.append("no chapter summaries found (selector may have changed)")
    return warnings


def save_session(result: dict, output_root: Path):
    out_dir = output_root / result["year"] / result["session_id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {k: v for k, v in result.items() if k not in ("transcript", "code_snippets")}
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    if result["transcript"]:
        (out_dir / "transcript.md").write_text(f"# {result['title']}\n\n{result['transcript']}")

    if result["code_snippets"]:
        snippets_dir = out_dir / "snippets"
        snippets_dir.mkdir(exist_ok=True)
        for i, snippet in enumerate(result["code_snippets"], 1):
            ts = re.sub(r"[^\w]", "_", snippet["timestamp"])
            content = f"// {snippet['timestamp']}\n{snippet['code']}"
            (snippets_dir / f"{i:02d}_{ts}.swift").write_text(content)

    return out_dir


def already_scraped(session: dict, output_root: Path) -> bool:
    marker = output_root / session["year"] / session["session_id"] / "metadata.json"
    return marker.exists()


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def batch_scrape(listing_url: str, output_root: Path, delay: float = DEFAULT_DELAY):
    sessions = discover_sessions(listing_url)
    total = len(sessions)
    skipped = sum(1 for s in sessions if already_scraped(s, output_root))
    to_scrape = [s for s in sessions if not already_scraped(s, output_root)]

    print(f"\n{skipped} already scraped, {len(to_scrape)} remaining.\n")

    results = {"ok": [], "failed": []}

    for i, session in enumerate(to_scrape, 1):
        prefix = f"[{i}/{len(to_scrape)}]"
        print(f"{prefix} {session['session_id']} — {session['title'][:60]}", end="", flush=True)
        try:
            result = scrape_session(session["url"])
            warnings = validate_session(result)
            out_dir = save_session(result, output_root)
            warn_str = f"  ⚠ {'; '.join(warnings)}" if warnings else ""
            print(f"  ✓  ({result['transcript_paragraph_count']}p transcript, {result['code_snippet_count']} snippets){warn_str}")
            results["ok"].append(session["session_id"])
            if warnings:
                results.setdefault("warnings", []).append({
                    "session_id": session["session_id"],
                    "title": result["title"],
                    "warnings": warnings,
                })
        except Exception as e:
            print(f"  ✗  {e}")
            results["failed"].append({"session_id": session["session_id"], "error": str(e)})

        if i < len(to_scrape):
            time.sleep(delay)

    print(f"\nDone. {len(results['ok'])}/{len(to_scrape)} succeeded.")
    if results.get("warnings"):
        print(f"\n⚠  {len(results['warnings'])} session(s) with unexpected structure:")
        for w in results["warnings"]:
            print(f"   {w['session_id']} {w['title']}: {'; '.join(w['warnings'])}")
        print("   If multiple sessions show the same warning, Apple may have changed their HTML.")
    if results["failed"]:
        print(f"Failed: {[f['session_id'] for f in results['failed']]}")

    # Save run report
    report_path = output_root / f"run_report_{int(time.time())}.json"
    report_path.write_text(json.dumps(results, indent=2))
    print(f"Report saved to {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_session_summary(result: dict, out_dir: Path):
    print(f"\n{'='*50}")
    print(f"Title:       {result['title']}")
    print(f"Session:     WWDC{result['year']} #{result['session_id']}")
    print(f"Description: {result['description'][:120]}...")
    print(f"Transcript:  {result['transcript_paragraph_count']} paragraphs")
    print(f"Code:        {result['code_snippet_count']} snippets")
    print(f"Chapters:    {len(result['chapters'])}")
    print(f"Summaries:   {len(result['chapter_summaries'])} chapter summaries")
    print(f"Output:      {out_dir}/")
    print(f"{'='*50}")
    if result["transcript"]:
        print(f"\nTranscript preview:\n{result['transcript'][:400]}...")
    if result["chapter_summaries"]:
        print(f"\nChapter summaries:")
        for s in result["chapter_summaries"]:
            print(f"  {s[:120]}")
    if result["code_snippets"]:
        first = result["code_snippets"][0]
        print(f"\nFirst snippet [{first['timestamp']}]:\n{first['code'][:300]}")


def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    output_root = Path(__file__).parent.parent / "output"

    if args[0] == "--discover":
        raw = args[1] if len(args) > 1 else "2025"
        url = raw if raw.startswith("http") else f"https://developer.apple.com/videos/wwdc{raw}/"
        sessions = discover_sessions(url)
        for s in sessions:
            print(f"  {s['session_id']}  {s['url']}  {s['title']}")

    elif args[0] == "--all":
        raw = args[1] if len(args) > 1 else "2025"
        # Accept a bare year ("2024") or a full URL
        url = raw if raw.startswith("http") else f"https://developer.apple.com/videos/wwdc{raw}/"
        delay = float(args[2]) if len(args) > 2 else DEFAULT_DELAY
        batch_scrape(url, output_root, delay=delay)

    else:
        url = args[0]
        print(f"Fetching {url}...")
        result = scrape_session(url)
        out_dir = save_session(result, output_root)
        print_session_summary(result, out_dir)


if __name__ == "__main__":
    main()
