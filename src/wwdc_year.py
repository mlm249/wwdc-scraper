"""
WWDC year resolution.

WWDC is always held in the first or second week of June.
We use June 15 as a conservative cutoff — if today is past that date,
the current year's WWDC has almost certainly happened.
"""

from datetime import date
from pathlib import Path

WWDC_LIKELY_PAST_MONTH = 6
WWDC_LIKELY_PAST_DAY = 15  # buffer past earliest possible WWDC date


def current_wwdc_year() -> int:
    """
    The WWDC year we'd expect to be current right now based on the calendar.
    Returns current year if past June 15, otherwise last year.
    """
    today = date.today()
    if today.month > WWDC_LIKELY_PAST_MONTH or (
        today.month == WWDC_LIKELY_PAST_MONTH and today.day >= WWDC_LIKELY_PAST_DAY
    ):
        return today.year
    return today.year - 1


def resolve_year(data_root: Path) -> tuple[str, str | None]:
    """
    Returns (year_str, warning_or_none).

    Logic:
      1. If data exists for the expected current WWDC year → use it.
      2. If no data exists but we're post-June 15 → return current year + warn.
      3. If pre-June 15 and no data for last year → fall back to most recent
         year that has data.
    """
    sessions_root = data_root / "sessions" if (data_root / "sessions").exists() else data_root

    expected = current_wwdc_year()
    today = date.today()

    # Check expected year first
    if (sessions_root / str(expected)).exists():
        return str(expected), None

    # No data for expected year
    post_wwdc = today.month > WWDC_LIKELY_PAST_MONTH or (
        today.month == WWDC_LIKELY_PAST_MONTH and today.day >= WWDC_LIKELY_PAST_DAY
    )

    if post_wwdc:
        # WWDC has happened but we haven't scraped yet
        warning = (
            f"WWDC {expected} data not found. "
            f"It's past June 15 — run `make scrape YEAR={expected}` to fetch this year's sessions."
        )
        # Still try to find most recent available year to fall back on
    else:
        warning = None

    # Find most recent year with data
    available = sorted(
        (int(p.name) for p in sessions_root.iterdir() if p.is_dir() and p.name.isdigit()),
        reverse=True,
    ) if sessions_root.exists() else []

    if available:
        fallback = str(available[0])
        if warning:
            warning += f" Falling back to WWDC {fallback}."
        return fallback, warning

    return str(expected), f"No session data found. Run `make scrape` to get started."


if __name__ == "__main__":
    import os
    from pathlib import Path
    data_root = Path(os.environ.get("WWDC_DOCS_PATH", Path(__file__).parent.parent / "output"))
    year, warning = resolve_year(data_root)
    print(f"Resolved year: {year}")
    if warning:
        print(f"Warning: {warning}")
    print(f"Expected WWDC year (calendar): {current_wwdc_year()}")
