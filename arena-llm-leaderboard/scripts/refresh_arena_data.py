#!/usr/bin/env python3
"""Capture every Arena agent-leaderboard category with all columns expanded.

The wide table does not exist until the page is driven: the server sends a default
column set, the ``Edit columns`` panel keeps its switches in React state, and the
category list is client-side navigation.  This module therefore opens a real browser,
walks the ``Categories`` list in the left sidebar, switches to every category, turns
every column switch **on**, closes the panel again, and returns one full HTML
snapshot per category.

This is a *module*, not an entry point: ``export_arena_leaderboard.py`` imports
:func:`capture_documents` and is the single documented command (``--dry-run`` covers
"capture without writing").  Importing it does not require Playwright - that is
imported inside :func:`capture_documents` - so ``--offline`` runs on a machine
without it.

``--channel`` (exposed by the entry point) defaults to ``auto``: it tries the bundled
chromium first and falls back to the locally installed Chrome or Edge, so a stalled
``cdn.playwright.dev`` download is not fatal.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The parser is the single source of truth for the column contract and for the
# category badge format.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_arena_leaderboard as arena  # noqa: E402

DEFAULT_URL = "https://arena.ai/leaderboard/agent/overall"

#: Used only when the live sidebar cannot be read (the page always lists these).
FALLBACK_CATEGORIES = ("Overall", "Code", "Chat", "Work")

#: The table hydrates on the client, so wait for React to render rows first.
ROWS_PRESENT = "document.querySelectorAll('tbody tr').length > 0"

#: Text sampled from the page to recognise a bot-check interstitial.
CHALLENGE_TEXT = (
    "document.body ? document.body.innerText.slice(0, 4000) : ''"
)

#: The heading badge ("Agent Arena <emoji> <Category>") is the category indicator.
H1_HAS_NAME = (
    "name => { const h = document.querySelector('h1');"
    " return !!h && (h.innerText || '').includes(name); }"
)

TAG_CATEGORIES = """
() => {
  const aside = document.querySelector('aside');
  if (!aside) return [];
  let root = aside;
  const header = Array.from(aside.querySelectorAll('button')).find(
    (b) => (b.innerText || '').trim().startsWith('Categories'));
  if (header) {
    const region = document.getElementById(header.getAttribute('aria-controls') || '');
    if (region) root = region;
  }
  const found = [];
  for (const button of root.querySelectorAll('button')) {
    const label = (button.innerText || '').trim();
    const name = label.replace(/^\\S+\\s*/, '');
    if (!name || name.startsWith('Categories')) continue;
    button.setAttribute('data-arena-category', name);
    found.push({name: name, active: !!button.querySelector('svg.lucide-check')});
  }
  return found;
}
"""

UNTAG_CATEGORIES = """
() => {
  document.querySelectorAll('[data-arena-category]')
    .forEach((el) => el.removeAttribute('data-arena-category'));
}
"""

LIST_SWITCHES = """
() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return null;
  return Array.from(dialog.querySelectorAll('button[role="switch"]')).map((s) => ({
    label: ((s.closest('div.flex') || s.parentElement).innerText || '').split('\\n')[0].trim(),
    state: s.getAttribute('data-state'),
  }));
}
"""

PLAYWRIGHT_HINT = (
    "Playwright is not installed.\n"
    "  pip install playwright                      # then either:\n"
    "  python -m playwright install chromium       # bundled browser (~150 MB), or\n"
    "  --channel chrome                            # reuse the installed Chrome"
)

#: arena.ai sits behind Cloudflare.  A burst of captures (or any retry loop) gets an
#: interstitial instead of the leaderboard, which would otherwise look like a plain
#: timeout.
CHALLENGE_MARKERS = (
    "just a moment", "verifying you are human", "checking your browser",
    "enable javascript and cookies", "cf_chl", "security verification",
    "\u5b89\u5168\u9a8c\u8bc1", "\u8bf7\u7a0d\u5019", "\u6b63\u5728\u8fdb\u884c\u5b89\u5168\u9a8c\u8bc1",
)

CHALLENGE_HINT = (
    "arena.ai returned a bot-check page instead of the leaderboard. The site is "
    "behind Cloudflare: several captures in a row trigger an HTTP 429 challenge. "
    "Wait a few minutes before running the browser step again - do not retry in a "
    "loop - or re-parse the snapshots you already have with --offline."
)

_BADGE_RE = re.compile(r"Agent Arena\s*(.*)$", re.S)
_EMOJI_RE = re.compile(r"^[^\w(]+", re.UNICODE)


class PlaywrightMissing(RuntimeError):
    """Playwright (the Python package) is not importable."""


class CaptureError(RuntimeError):
    """The browser could not be launched, or a category/column step failed."""


def category_badge(document: str) -> str:
    """Return the category name shown in the page heading of a snapshot."""
    match = re.search(r"<h1\b.*?</h1>", document, re.S)
    text = arena.cell_text(match.group(0)) if match else ""
    badge = _BADGE_RE.search(text)
    if not badge:
        return ""
    return _EMOJI_RE.sub("", badge.group(1).strip()).strip()


def capture_documents(
    url: str = DEFAULT_URL,
    categories: list[str] | None = None,
    channel: str = "auto",
    timeout_ms: float = 30_000,
    log=None,
) -> list[tuple[str, str]]:
    """Return ``[(category, html), ...]`` with every column expanded.

    ``categories`` limits and orders the walk; ``None`` uses every category the live
    sidebar lists (falling back to :data:`FALLBACK_CATEGORIES`).  Raises
    :class:`PlaywrightMissing` / :class:`CaptureError`.
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
    captured: list[tuple[str, str]] = []

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

            page = browser.new_page(viewport={"width": 1600, "height": 950})
            page.set_default_timeout(timeout_ms)
            response = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            blocked = _challenge(page, response)
            if blocked:
                raise CaptureError(f"{blocked}: {CHALLENGE_HINT}")
            _wait_for_rows(page, "start", timeout_ms, PlaywrightError)
            page.wait_for_timeout(1500)

            found = page.evaluate(TAG_CATEGORIES) or []
            if not found:
                note("sidebar categories not found; using the default list")
                found = [{"name": name, "active": name == "Overall"}
                         for name in FALLBACK_CATEGORIES]
            available = [entry["name"] for entry in found]
            note(f"categories on the page: {', '.join(available)}")

            wanted = categories or available
            unknown = [name for name in wanted if name not in available]
            if unknown:
                raise CaptureError(
                    f"category not listed on the page: {', '.join(unknown)} "
                    f"(available: {', '.join(available)})"
                )

            for name in wanted:
                note(f"[{name}] switching category")
                # React re-renders the sidebar after any interaction and drops the
                # attributes, so re-tag on every iteration before clicking.
                found = page.evaluate(TAG_CATEGORIES) or found
                active = next((entry["active"] for entry in found
                               if entry["name"] == name), False)
                if not active:
                    previous_url = page.url
                    page.click(f'[data-arena-category="{name}"]')
                    try:
                        page.wait_for_url(lambda current: current != previous_url,
                                          timeout=timeout_ms)
                    except PlaywrightError:
                        note(f"[{name}] note: the URL did not change; waiting on the "
                             "heading instead")
                page.wait_for_function(H1_HAS_NAME, arg=name, timeout=timeout_ms)

                # The client-side router keeps the previous categories mounted, so
                # the live DOM accumulates two or three stale copies of the whole
                # page.  Reload the category's own URL to snapshot one clean tree.
                reloaded = page.reload(wait_until="domcontentloaded", timeout=90_000)
                blocked = _challenge(page, reloaded)
                if blocked:
                    raise CaptureError(f"[{name}] {blocked}: {CHALLENGE_HINT}")
                page.wait_for_function(H1_HAS_NAME, arg=name, timeout=timeout_ms)
                _wait_for_rows(page, name, timeout_ms, PlaywrightError)
                page.wait_for_timeout(800)

                expanded = _expand_columns(page, name, PlaywrightError, note, timeout_ms)
                note(f"[{name}] columns expanded: {expanded}")

                page.evaluate(UNTAG_CATEGORIES)
                document = page.content()
                badge = category_badge(document)
                if badge and badge != name:
                    raise CaptureError(
                        f"[{name}] the captured page shows category {badge!r} - "
                        "aborting instead of mislabelling the snapshot"
                    )
                issues = _snapshot_issues(document)
                if issues:
                    raise CaptureError(f"[{name}] " + "; ".join(issues))
                captured.append((name, document))
                note(f"[{name}] captured {len(document):,} characters")

            browser.close()
    except (PlaywrightError, RuntimeError, CaptureError) as error:
        for attempt in launch_errors:
            note(f"launch attempt failed: {attempt}")
        if isinstance(error, CaptureError):
            raise
        raise CaptureError(str(error)) from error
    return captured


def _snapshot_issues(document: str) -> list[str]:
    """Return why a snapshot is not a clean single-page capture (empty when fine)."""
    issues: list[str] = []
    tables = len(re.findall(r"<table\b", document))
    headings = len(re.findall(r"<h1\b", document))
    if tables != 1:
        issues.append(
            f"the snapshot holds {tables} tables (expected 1) - the client-side "
            "router is still carrying previous categories; reload before capturing"
        )
    if headings != 1:
        issues.append(f"the snapshot holds {headings} <h1> headings (expected 1)")
    return issues


def _challenge(page, response) -> str | None:
    """Return a description when the page is a bot-check interstitial."""
    status = getattr(response, "status", None)
    if status in (403, 429):
        return f"HTTP {status}"
    if "__cf_chl" in page.url:
        return "a Cloudflare challenge URL"
    try:
        haystack = f"{page.title()}\n{page.evaluate(CHALLENGE_TEXT)}".lower()
    except Exception:  # noqa: BLE001 - the page may be mid-navigation
        return None
    for marker in CHALLENGE_MARKERS:
        if marker in haystack:
            return f"a Cloudflare interstitial ({marker!r})"
    return None


def _wait_for_rows(page, name: str, timeout_ms: float, playwright_error) -> None:
    """Wait for the hydrated table, reporting a challenge or a slow page clearly."""
    try:
        page.wait_for_function(ROWS_PRESENT, timeout=timeout_ms)
    except playwright_error as error:
        blocked = _challenge(page, None)
        if blocked:
            raise CaptureError(f"[{name}] {blocked}: {CHALLENGE_HINT}") from error
        raise CaptureError(
            f"[{name}] the leaderboard table never rendered within "
            f"{timeout_ms:.0f} ms ({str(error).splitlines()[0]}); retry with a larger "
            "--timeout, or re-parse an existing snapshot with --offline"
        ) from error


def _expand_columns(page, name: str, playwright_error, note, timeout_ms: float) -> int:
    """Open the Edit-columns panel and switch every column on.

    Returns the number of switches that were flipped.  Raises :class:`CaptureError`
    when the panel never opens or a switch refuses to turn on.
    """
    gear = page.locator('button[aria-label="Edit columns"]:visible')
    if gear.count() == 0:
        raise CaptureError(f"[{name}] the 'Edit columns' button is not on the page")
    gear.first.click()
    page.wait_for_selector('[role="dialog"] button[role="switch"]',
                           state="visible", timeout=timeout_ms)

    switches = page.locator('[role="dialog"] button[role="switch"]')
    total = switches.count()
    flipped = 0
    for _ in range(total * 3):
        target = None
        for index in range(switches.count()):
            if switches.nth(index).get_attribute("data-state") != "checked":
                target = index
                break
        if target is None:
            break
        switches.nth(target).scroll_into_view_if_needed()
        switches.nth(target).click()
        page.wait_for_timeout(250)
        flipped += 1

    states = page.evaluate(LIST_SWITCHES) or []
    off = [entry["label"] for entry in states if entry["state"] != "checked"]
    if off:
        raise CaptureError(
            f"[{name}] column switches refused to turn on: {', '.join(off)}"
        )
    note(f"[{name}] {len(states)} column switches, all on ({flipped} flipped)")

    page.keyboard.press("Escape")
    try:
        page.wait_for_selector('[role="dialog"]', state="detached", timeout=5_000)
    except playwright_error:
        note(f"[{name}] note: the column panel stayed open in the snapshot")
    page.wait_for_timeout(400)
    return flipped


# --------------------------------------------------------------------------- #
# Module note
# --------------------------------------------------------------------------- #
# No CLI here on purpose: ``export_arena_leaderboard.py`` imports
# ``capture_documents`` and is the single documented entry point.
