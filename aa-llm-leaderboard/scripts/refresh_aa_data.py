#!/usr/bin/env python3
"""Capture the expanded Artificial Analysis leaderboard DOM.

The 43-column ("expanded") leaderboard only exists after a client-side click on the
*Expand columns* button: the server-rendered page ships just 8 metric columns, and
the expansion state lives in React state (it is neither a query parameter nor
persisted in local storage).  This module therefore drives a real browser, waits for
the hydrated table, clicks the button, waits for the wide table, and validates the
header anchors, the cell shapes and the row invariants.

This is a *module*, not an entry point: ``export_aa_leaderboard.py`` imports
:func:`capture_document` / :func:`validate_snapshot` / :func:`translation_warning` and
is the single documented command (``--dry-run`` covers "capture without writing").
Importing it does not require Playwright - that is imported inside
:func:`capture_document` - so ``--offline`` runs on a machine without it.

``--channel`` (exposed by the entry point) defaults to ``auto``: it tries the bundled
chromium first and then falls back to locally installed Chrome (``chrome``) or Edge
(``msedge``), so a stalled ``cdn.playwright.dev`` download is not fatal.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The parser is the single source of truth for the column contract.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_aa_leaderboard as aa  # noqa: E402

DEFAULT_URL = "https://artificialanalysis.ai/leaderboards/models"

EXPANDED_COLUMN_COUNT = len(aa.COLUMN_PLAN) + 1

# The table hydrates on the client, so the button only exists once React has
# rendered the rows - wait for that before clicking.
WAIT_FOR_TABLE = (
    "document.querySelectorAll('tbody tr').length > 0"
    " && document.querySelectorAll('thead th').length > 0"
)

WIDE_TABLE_CHECK = (
    "Array.from(document.querySelectorAll('thead tr'))"
    f".some(row => row.querySelectorAll('th, td').length >= {EXPANDED_COLUMN_COUNT})"
)

WAIT_FOR_WIDE_TABLE = WIDE_TABLE_CHECK

PLAYWRIGHT_HINT = (
    "Playwright is not installed.\n"
    "  pip install playwright                      # then either:\n"
    "  python -m playwright install chromium       # bundled browser (~150 MB), or\n"
    "  --channel chrome                            # reuse the installed Chrome"
)


class PlaywrightMissing(RuntimeError):
    """Playwright (the Python package) is not importable."""


class CaptureError(RuntimeError):
    """The browser could not be launched or the table could not be expanded."""


def capture_document(
    url: str = DEFAULT_URL,
    channel: str = "auto",
    timeout_ms: float = 30_000,
    log=None,
) -> str:
    """Drive a browser and return the DOM with the columns expanded.

    Raises :class:`PlaywrightMissing` when Playwright is absent and
    :class:`CaptureError` when every browser channel or the capture itself fails.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise PlaywrightMissing(PLAYWRIGHT_HINT) from error

    def note(message: str) -> None:
        if log is not None:
            log(message)

    candidates = (None, "chrome", "msedge") if channel == "auto" else \
        (None if channel == "chromium" else channel,)
    launch_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = None
            for candidate in candidates:
                try:
                    browser = playwright.chromium.launch(channel=candidate)
                    break
                except Exception as error:  # noqa: BLE001 - report every attempt
                    launch_errors.append(
                        f"{candidate or 'bundled chromium'}: {str(error).splitlines()[0]}"
                    )
            if browser is None:
                raise CaptureError("no usable browser; " + "; ".join(launch_errors))

            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_function(WAIT_FOR_TABLE, timeout=timeout_ms)
            expand_button = page.get_by_role("button", name="Expand columns")
            for attempt in range(2):
                if page.evaluate(WIDE_TABLE_CHECK):
                    break
                try:
                    expand_button.click(timeout=15_000)
                except PlaywrightError:
                    if attempt:
                        raise
                try:
                    page.wait_for_function(WIDE_TABLE_CHECK, timeout=10_000)
                except PlaywrightError:
                    # Only click again while the page still shows "Expand columns":
                    # clicking a second time after a slow expansion would collapse
                    # the table again.
                    if expand_button.count() == 0 or attempt:
                        break
            page.wait_for_function(WAIT_FOR_WIDE_TABLE, timeout=timeout_ms)
            document = page.content()
            browser.close()
    except (PlaywrightError, RuntimeError, CaptureError) as error:
        for attempt in launch_errors:
            note(f"launch attempt failed: {attempt}")
        if isinstance(error, CaptureError):
            raise
        raise CaptureError(str(error)) from error
    return document


def validate_snapshot(
    document: str, check_invariants: bool = True,
) -> tuple[list[str], list[list[str]], int, list[str], list[str]]:
    """Validate a captured document against the whole contract.

    Returns ``(leaf_headers, body_rows, checked_cells, anchor_issues, cell_problems)``;
    raises :class:`parse_aa_leaderboard.TableError` when no table is present.
    """
    leaf_headers, body_rows = aa.parse_table(document)
    anchor_issues = aa.validate_columns(leaf_headers)
    if anchor_issues:
        return leaf_headers, body_rows, 0, anchor_issues, []
    problems, checked = aa.validate_cells(body_rows, check_invariants=check_invariants)
    return leaf_headers, body_rows, checked, [], problems


def translation_warning(document: str) -> str | None:
    """Return a warning when a translation extension polluted the capture."""
    marker = document.find('imt-state="')
    if marker < 0:
        return None
    state = document[marker + len('imt-state="'):document.find('"', marker + 11)]
    if state == "original":
        return None
    return (f"imt-state={state!r} - a translation extension may have altered "
            "the captured text")


# --------------------------------------------------------------------------- #
# Module note
# --------------------------------------------------------------------------- #
# No CLI here on purpose: ``export_aa_leaderboard.py`` imports
# ``capture_document`` / ``validate_snapshot`` / ``translation_warning`` and is the
# single documented entry point (``--dry-run`` covers "capture without writing").
