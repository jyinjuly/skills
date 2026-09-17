#!/usr/bin/env python3
"""Parse an Arena agent-leaderboard HTML snapshot into the arena_data JSON shape.

Output schema (this mirrors ``arena_data_example.json``)::

    {
      "Date": "Sep 16, 2026",
      "Sessions": "1,850,083",
      "Models": "46",
      "Data": [
        {
          "Rank": {"Num": "1", "Min": "1", "Max": "2"},
          "Model": {"Name": "Claude Fable 5.1 (Max)",
                    "ProviderAndLicense": "Anthropic \u00b7 Proprietary"},
          "Net Improvement": {"Value": "+13.71%", "Range": "\u00b11.72%"},
          "Confirmed Success": {"Value": "+19.83%", "Range": "\u00b12.75%"},
          "Praise vs Complaint": {"Value": "+31.83%", "Range": "\u00b16.86%"},
          "Steerability": {"Value": "+3.88%", "Range": "\u00b13.61%"},
          "Bash Recovery": {"Value": "+12.62%", "Range": "\u00b10.76%"},
          "Tool Hallucination": {"Value": "+0.37%", "Range": "\u00b10.03%"},
          "Sessions": "13,320",
          "Cost/Task (P25)": "$0.94",
          "Cost/Task (P50)": "$4.33",
          "Cost/Task (P95)": "$35.96",
          "Output Tokens/Task (P25)": "10K",
          "Output Tokens/Task (P50)": "53.2K",
          "Output Tokens/Task (P95)": "516.6K",
          "Price $/M": "$10 / $50"
        }
      ]
    }

Columns are matched by **header text**, never by position, so the export survives
the optional columns being toggled on or off in the page's ``Edit columns`` panel.
Every column carries an anchor rule (``exact`` / ``prefix`` / ``contains``) and every
cell carries a shape rule, so a renamed or added column fails loudly instead of
silently shifting values into the wrong key.

Values are exported **verbatim as display strings** (``"53.2K"``, ``"$10 / $50"``,
``"N/A"``).  The one derived value is the sign of a score: the site renders every
score as an absolute percentage plus an arrow/colour, so ``13.71%`` with the green
("improvement") treatment becomes ``"+13.71%"`` and a red one becomes ``"-0.47%"``.
Note that the arrow direction is *not* the sign - for ``Tool Hallucination`` a
decreasing value is good, so it is green while its arrow points down, and the schema
records it as ``"+0.37%"`` (an improvement).

Standard library only.
"""

from __future__ import annotations

import html
import re

# --------------------------------------------------------------------------- #
# Column contract (the single place to edit when the leaderboard changes)
# --------------------------------------------------------------------------- #

#: Top-level key order of the exported document.
TOP_LEVEL_ORDER = ("Date", "Sessions", "Models", "Data")

#: Key order inside one record, i.e. the full "all columns enabled" layout.
RECORD_KEY_ORDER = (
    "Rank",
    "Model",
    "Net Improvement",
    "Confirmed Success",
    "Praise vs Complaint",
    "Steerability",
    "Bash Recovery",
    "Tool Hallucination",
    "Sessions",
    "Cost/Task (P25)",
    "Cost/Task (P50)",
    "Cost/Task (P95)",
    "Output Tokens/Task (P25)",
    "Output Tokens/Task (P50)",
    "Output Tokens/Task (P95)",
    "Price $/M",
)

#: Values the site renders when a metric was not measured.  They are exported
#: verbatim (the schema keeps the display string), but shape checks accept them.
PLACEHOLDERS = frozenset({"N/A", "--", "-", "\u2014"})

#: ``(anchor kind, anchor text, output key, cell kind)``.
#: Anchor kinds: ``exact`` | ``prefix`` | ``contains``; cell kinds: ``rank`` |
#: ``model`` | ``score`` | ``integer`` | ``usd`` | ``tokens`` | ``price``.
COLUMN_PLAN: tuple[tuple[str, str, str, str], ...] = (
    ("exact", "Rank", "Rank", "rank"),
    ("exact", "Model", "Model", "model"),
    ("exact", "Net Improvement", "Net Improvement", "score"),
    ("exact", "Confirmed Success", "Confirmed Success", "score"),
    ("exact", "Praise vs Complaint", "Praise vs Complaint", "score"),
    ("exact", "Steerability", "Steerability", "score"),
    ("exact", "Bash Recovery", "Bash Recovery", "score"),
    ("exact", "Tool Hallucination", "Tool Hallucination", "score"),
    ("exact", "Sessions", "Sessions", "integer"),
    ("prefix", "Cost/Task (P25)", "Cost/Task (P25)", "usd"),
    ("prefix", "Cost/Task (P50)", "Cost/Task (P50)", "usd"),
    ("prefix", "Cost/Task (P95)", "Cost/Task (P95)", "usd"),
    ("prefix", "Output Tokens/Task (P25)", "Output Tokens/Task (P25)", "tokens"),
    ("prefix", "Output Tokens/Task (P50)", "Output Tokens/Task (P50)", "tokens"),
    ("prefix", "Output Tokens/Task (P95)", "Output Tokens/Task (P95)", "tokens"),
    ("exact", "Price $/M", "Price $/M", "price"),
)

#: Cell shapes.  Every shape also accepts an empty cell or a placeholder.
SHAPES: dict[str, re.Pattern[str]] = {
    "rank": re.compile(r"\d+ \d+ \d+"),
    "model-name": re.compile(r"\S(.|\s)*"),
    "provider": re.compile(r".+ \u00b7 .+"),
    "score": re.compile(r"[+\-\u2212]?\d+(\.\d+)?%"),
    "range": re.compile(r"\u00b1\d+(\.\d+)?%"),
    "integer": re.compile(r"\d{1,3}(,\d{3})*|\d+"),
    "usd": re.compile(r"\$\d+(\.\d+)?$"),
    "tokens": re.compile(r"\d+(\.\d+)?[KMkm]?"),
    "price": re.compile(r"\$\d+(\.\d+)? / \$\d+(\.\d+)?$"),
}

#: Header-summary patterns, searched in the block that follows ``</h1>``.
_DATE_RE = re.compile(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})\b")
_SESSIONS_RE = re.compile(r"([\d,]+)\s+sessions")
_MODELS_RE = re.compile(r"(\d+)\s+models")

_EPSILON = 1e-6

_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.S)
_SECTION_RE = re.compile(r"<(thead|tbody)\b.*?</\1>", re.S)
_TH_RE = re.compile(r"<th\b.*?</th>", re.S)
_TR_RE = re.compile(r"<tr\b.*?</tr>", re.S)
_TD_RE = re.compile(r"<td\b.*?</td>", re.S)
_TITLE_RE = re.compile(r'title="([^"]*)"')
_PROVIDER_RE = re.compile(
    r'<span class="[^"]*truncate text-xs[^"]*"[^>]*>(.*?)</span>', re.S
)


class TableError(Exception):
    """The leaderboard table could not be located or has an unexpected shape."""


class RowError(Exception):
    """A data row does not have the expected number of cells."""


# --------------------------------------------------------------------------- #
# HTML extraction
# --------------------------------------------------------------------------- #

def cell_text(cell_html: str) -> str:
    """Return the visible text of one cell, with entities resolved."""
    return " ".join(html.unescape(_TAG_RE.sub(" ", cell_html)).split())


def number(text: str) -> float | None:
    """Parse a display string as a number, or return ``None``."""
    stripped = (text.replace("$", "").replace("%", "").replace(",", "")
                .replace("\u00b1", "").replace("+", "").replace("\u2212", "-")
                .strip())
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def parse_table(document: str) -> tuple[list[str], list[list[str]]]:
    """Return ``(header texts, body rows of raw cell HTML)`` for the first table.

    Raises :class:`TableError` when the document holds no table with a header row.
    """
    table = _TABLE_RE.search(document)
    if table is None:
        raise TableError("no <table> found - is this really a leaderboard snapshot?")
    sections = {name: body for name, body in
                ((m.group(1), m.group(0)) for m in _SECTION_RE.finditer(table.group(0)))}
    head = sections.get("thead")
    if head is None:
        raise TableError("the table has no <thead> row")
    headers = [cell_text(cell) for cell in _TH_RE.findall(head)]
    if not headers:
        raise TableError("the table header row is empty")
    body = sections.get("tbody")
    if body is None:
        raise TableError("the table has no <tbody>")
    rows = [cells for cells in
            (_TD_RE.findall(row) for row in _TR_RE.findall(body)) if cells]
    if not rows:
        raise TableError("the table body has no rows")
    return headers, rows


def extract_meta(document: str) -> dict[str, str]:
    """Return the page-level summary (``Date`` / ``Sessions`` / ``Models``)."""
    end = document.find("</h1>")
    region = document[end:end + 8000] if end >= 0 else document
    meta = {"Date": "", "Sessions": "", "Models": ""}
    for key, pattern in (("Date", _DATE_RE), ("Sessions", _SESSIONS_RE),
                         ("Models", _MODELS_RE)):
        match = pattern.search(region) or pattern.search(document)
        if match:
            meta[key] = match.group(1)
    return meta


# --------------------------------------------------------------------------- #
# Contract validation
# --------------------------------------------------------------------------- #

def _match_anchor(header: str, kind: str, anchor: str) -> bool:
    if kind == "exact":
        return header == anchor
    if kind == "prefix":
        return header.startswith(anchor)
    return anchor in header


def validate_columns(
    headers: list[str], allow_partial: bool = False,
) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Map header cells onto the column contract.

    Returns ``(columns, issues)`` where ``columns`` is a list of
    ``(cell index, output key, cell kind)`` in page order.  ``issues`` is non-empty
    when a header is unknown, a contract column is missing, or two headers claim the
    same key; a missing column is only an issue when ``allow_partial`` is false.
    """
    issues: list[str] = []
    columns: list[tuple[int, str, str]] = []
    claimed: dict[str, int] = {}

    for index, header in enumerate(headers):
        matches = [entry for entry in COLUMN_PLAN
                   if _match_anchor(header, entry[0], entry[1])]
        if not matches:
            issues.append(f"header {index}: unknown column {header!r} - "
                          "add it to COLUMN_PLAN (or stop expanding the table)")
            continue
        kind, anchor, key, cell_kind = matches[0]
        if len(matches) > 1:
            issues.append(f"header {index}: {header!r} is ambiguous between "
                          + ", ".join(repr(entry[2]) for entry in matches))
            continue
        if key in claimed:
            issues.append(f"header {index}: {header!r} duplicates the column "
                          f"already claimed by header {claimed[key]}")
            continue
        claimed[key] = index
        columns.append((index, key, cell_kind))

    if not allow_partial:
        missing = [key for key in RECORD_KEY_ORDER if key not in claimed]
        if missing:
            issues.append(
                "captured without every column expanded - missing "
                + ", ".join(repr(key) for key in missing)
                + " (re-capture with the Edit columns panel fully checked, or pass "
                  "--allow-partial to export the subset)"
            )
    return columns, issues


def _check(problems: list[str], where: str, value: str, shape: str) -> None:
    if value == "" or value in PLACEHOLDERS:
        return
    pattern = SHAPES[shape]
    if not pattern.fullmatch(value):
        problems.append(f"{where}: {value!r} does not look like a {shape} value")


# --------------------------------------------------------------------------- #
# Record building
# --------------------------------------------------------------------------- #

def _rank_cell(cell: str, where: str, problems: list[str]) -> dict[str, str]:
    text = cell_text(cell)
    _check(problems, where, text, "rank")
    numbers = re.findall(r"\d+", text)
    if len(numbers) < 3:
        return {"Num": numbers[0] if numbers else "", "Min": "", "Max": ""}
    return {"Num": numbers[0], "Min": numbers[1], "Max": numbers[2]}


def _model_cell(cell: str, where: str, problems: list[str]) -> dict[str, str]:
    titles = _TITLE_RE.findall(cell)
    name = html.unescape(titles[-1]).strip() if titles else ""
    provider_match = _PROVIDER_RE.search(cell)
    provider = html.unescape(provider_match.group(1)).strip() if provider_match else ""
    if not name:
        # No title attribute anywhere: strip the provider line off the cell text.
        text = cell_text(cell)
        name = text[:text.rfind(provider)].strip() if provider and provider in text else text
    _check(problems, f"{where}.Name", name, "model-name")
    _check(problems, f"{where}.ProviderAndLicense", provider, "provider")
    return {"Name": name, "ProviderAndLicense": provider}


def _score_cell(cell: str, where: str, problems: list[str]) -> dict[str, str]:
    text = cell_text(cell)
    value_match = re.search(r"(\d+(?:\.\d+)?)%", text)
    range_match = re.search(r"\u00b1\s*(\d+(?:\.\d+)?)%", text)
    sign = ""
    if "text-interactive-negative" in cell:
        sign = "-"
    elif "text-interactive-positive" in cell:
        sign = "+"
    value = f"{sign}{value_match.group(1)}%" if value_match else ""
    span = f"\u00b1{range_match.group(1)}%" if range_match else ""
    _check(problems, f"{where}.Value", value, "score")
    _check(problems, f"{where}.Range", span, "range")
    return {"Value": value, "Range": span}


def _scalar_cell(cell: str, kind: str, where: str, problems: list[str]) -> str:
    text = cell_text(cell)
    _check(problems, where, text, kind)
    return text


def build_records(
    columns: list[tuple[int, str, str]],
    rows: list[list[str]],
    limit: int | None = None,
    check_invariants: bool = True,
) -> tuple[list[dict], list[str], int]:
    """Turn raw rows into records.

    Returns ``(records, problems, checked)``; raises :class:`RowError` when a row's
    cell count does not match the header's.
    """
    width = max(index for index, _key, _kind in columns) + 1
    problems: list[str] = []
    records: list[dict] = []
    checked = 0
    selected = rows if limit is None else rows[:limit]

    for position, row in enumerate(selected, start=1):
        if len(row) < width:
            raise RowError(
                f"row {position}: {len(row)} cells but the header has {width} "
                "columns - the snapshot is truncated"
            )
        record: dict[str, object] = {}
        for index, key, kind in columns:
            where = f"row {position} {key}"
            cell = row[index]
            if kind == "rank":
                record[key] = _rank_cell(cell, where, problems)
            elif kind == "model":
                record[key] = _model_cell(cell, where, problems)
            elif kind == "score":
                record[key] = _score_cell(cell, where, problems)
            else:
                record[key] = _scalar_cell(cell, kind, where, problems)
            checked += 1
        records.append({key: record[key] for key in RECORD_KEY_ORDER if key in record})

    if check_invariants:
        problems.extend(_check_invariants(records))
    return records, problems, checked


def _check_invariants(records: list[dict]) -> list[str]:
    """Data-independent checks that stay valid across every refresh."""
    problems: list[str] = []
    names: list[str] = []
    previous_rank: float | None = None

    for position, record in enumerate(records, start=1):
        name = (record.get("Model") or {}).get("Name", "")
        names.append(name)
        if not name:
            problems.append(f"row {position}: Model.Name is empty")

        rank = record.get("Rank")
        if isinstance(rank, dict):
            num, low, high = (number(rank.get("Num", "")), number(rank.get("Min", "")),
                              number(rank.get("Max", "")))
            if None not in (num, low, high):
                if low > num + _EPSILON or num > high + _EPSILON:
                    problems.append(
                        f"row {position}: Rank {num} is outside [{low}, {high}]"
                    )
                if previous_rank is not None and num < previous_rank - _EPSILON:
                    problems.append(
                        f"row {position}: Rank {num} decreases after {previous_rank} "
                        "- the row order is not ascending"
                    )
                previous_rank = num

    duplicates = {name for name in names if names.count(name) > 1 and name}
    if duplicates:
        problems.append("duplicate Model names: " + ", ".join(sorted(duplicates)))
    return problems


def build_payload(
    document: str,
    limit: int | None = None,
    check_invariants: bool = True,
    allow_partial: bool = False,
) -> tuple[dict, list[str], list[str], int]:
    """Parse a snapshot into the export payload.

    Returns ``(payload, anchor_issues, cell_problems, checked)``; raises
    :class:`TableError` when no table is present and :class:`RowError` for a
    malformed row.
    """
    headers, rows = parse_table(document)
    columns, issues = validate_columns(headers, allow_partial=allow_partial)
    if issues:
        return {}, issues, [], 0
    records, problems, checked = build_records(
        columns, rows, limit=limit, check_invariants=check_invariants,
    )
    payload = dict(extract_meta(document))
    payload["Data"] = records
    if check_invariants and limit is None and payload["Models"].isdigit():
        announced = int(payload["Models"])
        if announced != len(records):
            problems.append(
                f"the page announces {announced} models but the table has "
                f"{len(records)} rows - the roster is paginated, or a stale table "
                "was captured"
            )
    return {key: payload[key] for key in TOP_LEVEL_ORDER}, [], problems, checked
