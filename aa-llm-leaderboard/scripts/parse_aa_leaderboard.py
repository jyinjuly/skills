#!/usr/bin/env python3
"""Parse an Artificial Analysis LLM-leaderboard HTML snapshot into JSON.

Output schema (one record per table row, in page order)::

    [
      {
        "Model": "...",
        "Features": {"Context Window": ..., "Creator": ..., "License": ...},
        "Intelligence": { ...20 benchmark metrics... },
        "Price": {"Cost Per Task": ..., "Input Price": ..., "Output Price": ...,
                  "Cache Hit Price": ..., "Cache Write Price": ...},
        "Speed": {"Median": ..., "P5": ..., "P25": ..., "P75": ..., "P95": ...},
        "Latency": {"Latency": ..., "First Answer": ..., "P5": ..., "P25": ...,
                    "P75": ..., "P95": ...},
        "End-To-End Response Time": {"Total": ..., "Reasoning": ...}
      }
    ]

Columns are addressed by their position in the table row and every column carries
an *anchor rule* (checked against the header text) plus a *shape rule* (checked
against every cell), so a site change or a wrong key mapping fails loudly instead
of silently shifting values.  Cross-field invariants (percentile ordering, sort
order, response-time ordering) are checked too; they are data-independent, so they
never need re-baselining.

Values are exported verbatim as display strings; the placeholder ``--`` becomes an
empty string.  Standard library only.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# --------------------------------------------------------------------------- #
# Column contract (the single place to edit when the leaderboard changes)
# --------------------------------------------------------------------------- #

TOP_LEVEL_ORDER = (
    "Model",
    "Features",
    "Intelligence",
    "Price",
    "Speed",
    "Latency",
    "End-To-End Response Time",
)

#: Placeholder values that mean "not measured".
PLACEHOLDERS = frozenset({"--", "-", "", "N/A"})

#: The trailing, non-metric column of every row (Model/Providers links).
TRAILING_HEADER = "Further Analysis"

#: Cell shapes.  Every shape also accepts the empty string (unmeasured).
#: ``\u2212`` is the Unicode minus sign the site uses for negative indices.
SHAPES: dict[str, re.Pattern[str]] = {
    "text": re.compile(r"[^$%\r\n]{1,64}"),
    "license": re.compile(r"Open|Proprietary"),
    "tokens": re.compile(r"\d+(\.\d+)?[kM]"),
    "index": re.compile(r"\d+\*?"),
    "signed": re.compile(r"[\-\u2212]?\d+"),
    "percent": re.compile(r"[\-\u2212]?\d+(\.\d+)?%"),
    "usd": re.compile(r"\$\d+(\.\d+)?$"),
    "speed": re.compile(r"\d{1,3}(,\d{3})*"),
    "seconds": re.compile(r"\d+(\.\d+)?$"),
}

#: ``(row cell index, output path, anchor kind, anchor value, shape)``.
#: Anchor kinds: ``exact`` | ``prefix`` | ``contains``.
COLUMN_PLAN: tuple[tuple[int, tuple[str, ...], str, str, str], ...] = (
    (0, ("Model",), "exact", "Model", "text"),
    (1, ("Features", "Context Window"), "exact", "Context Window", "tokens"),
    (2, ("Features", "Creator"), "exact", "Creator", "text"),
    (3, ("Features", "License"), "exact", "License", "license"),
    (4, ("Intelligence", "Artificial Analysis Intelligence Index"), "exact",
     "Artificial Analysis Intelligence Index", "index"),
    (5, ("Intelligence", "Artificial Analysis Omniscience Index"), "prefix",
     "Artificial Analysis Omniscience Index", "signed"),
    (6, ("Intelligence", "GDPval-AA v2"), "prefix", "GDPval-AA v2", "percent"),
    (7, ("Intelligence", "AA-AnalystAgent"), "prefix", "AA-AnalystAgent", "percent"),
    (8, ("Intelligence", "Terminal-Bench Hard"), "prefix", "Terminal-Bench Hard", "percent"),
    (9, ("Intelligence", "Terminal-Bench 2.1"), "prefix", "Terminal-Bench 2.1", "percent"),
    (10, ("Intelligence", "Terminal-Bench 4.0"), "prefix", "Terminal-Bench 4.0", "percent"),
    # The two tau-bench headers are mojibake in some saved snapshots, so only a
    # stable ASCII fragment can be anchored.
    (11, ("Intelligence", "\u03c4\u00b2-Bench Telecom"), "contains", "Bench Telecom", "percent"),
    (12, ("Intelligence", "\U0001d70f\u00b3-Banking"), "contains", "Banking", "percent"),
    (13, ("Intelligence", "AA-LCR"), "exact", "AA-LCR Long Context Reasoning", "percent"),
    (14, ("Intelligence", "AA-Omniscience Accuracy"), "prefix",
     "AA-Omniscience Accuracy", "percent"),
    (15, ("Intelligence", "AA-Omniscience Non-Hallucination Rate"), "prefix",
     "AA-Omniscience Non-Hallucination Rate", "percent"),
    (16, ("Intelligence", "Humanity's Last Exam"), "exact",
     "Humanity's Last Exam Reasoning & Knowledge", "percent"),
    (17, ("Intelligence", "GPQA Diamond"), "prefix", "GPQA Diamond", "percent"),
    (18, ("Intelligence", "SciCode"), "prefix", "SciCode", "percent"),
    (19, ("Intelligence", "IFBench"), "prefix", "IFBench", "percent"),
    (20, ("Intelligence", "CritPt"), "prefix", "CritPt", "percent"),
    (21, ("Intelligence", "APEX-Agents-AA"), "prefix", "APEX-Agents-AA", "percent"),
    (22, ("Intelligence", "ITBench-AA"), "prefix", "ITBench-AA", "percent"),
    (23, ("Intelligence", "MMMU Pro"), "prefix", "MMMU Pro", "percent"),
    (24, ("Price", "Cost Per Task"), "exact", "Cost per Task USD", "usd"),
    (25, ("Price", "Input Price"), "prefix", "Input Price", "usd"),
    (26, ("Price", "Output Price"), "prefix", "Output Price", "usd"),
    (27, ("Price", "Cache Hit Price"), "prefix", "Cache Hit Price", "usd"),
    (28, ("Price", "Cache Write Price"), "prefix", "Cache Write Price", "usd"),
    (29, ("Speed", "Median"), "exact", "Median Tokens/s", "speed"),
    (30, ("Speed", "P5"), "prefix", "P5 Tokens/s", "speed"),
    (31, ("Speed", "P25"), "prefix", "P25 Tokens/s", "speed"),
    (32, ("Speed", "P75"), "prefix", "P75 Tokens/s", "speed"),
    (33, ("Speed", "P95"), "prefix", "P95 Tokens/s", "speed"),
    (34, ("Latency", "Latency"), "exact", "Latency First Chunk (s)", "seconds"),
    (35, ("Latency", "First Answer"), "exact", "First Answer (s)", "seconds"),
    (36, ("Latency", "P5"), "prefix", "P5 First Chunk (s)", "seconds"),
    (37, ("Latency", "P25"), "prefix", "P25 First Chunk (s)", "seconds"),
    (38, ("Latency", "P75"), "prefix", "P75 First Chunk (s)", "seconds"),
    (39, ("Latency", "P95"), "prefix", "P95 First Chunk (s)", "seconds"),
    (40, ("End-To-End Response Time", "Total"), "exact", "Total Response (s)", "seconds"),
    (41, ("End-To-End Response Time", "Reasoning"), "exact", "Reasoning Time (s)", "seconds"),
)

INDEX_BY_PATH = {entry[1]: entry[0] for entry in COLUMN_PLAN}

#: Ordering invariants that must hold inside every row (skipped where a value is
#: empty).  Data-independent, so they stay valid across web-site data drift.
ROW_INVARIANTS: tuple[tuple[tuple[tuple[str, str], ...], str], ...] = (
    ((("Speed", "P5"), ("Speed", "P25"), ("Speed", "Median"), ("Speed", "P75"),
      ("Speed", "P95")), "speed percentiles must not decrease"),
    ((("Latency", "P5"), ("Latency", "P25"), ("Latency", "Latency"),
      ("Latency", "P75"), ("Latency", "P95")), "latency percentiles must not decrease"),
    ((("Latency", "Latency"), ("Latency", "First Answer")),
     "first chunk latency must not exceed first answer"),
    ((("Latency", "First Answer"), ("End-To-End Response Time", "Total")),
     "first answer must not exceed total response time"),
    ((("End-To-End Response Time", "Reasoning"), ("End-To-End Response Time", "Total")),
     "reasoning time must not exceed total response time"),
)

#: The export is sorted by this metric, descending (ties allowed).
SORT_PATH = ("Intelligence", "Artificial Analysis Intelligence Index")

_EPSILON = 1e-6


class TableError(Exception):
    """The leaderboard table could not be located or has an unexpected shape."""


class RowError(Exception):
    """A data row does not have the expected number of cells."""


# --------------------------------------------------------------------------- #
# HTML extraction
# --------------------------------------------------------------------------- #

def _collapse(text: str) -> str:
    return " ".join(text.split())


def to_value(text: str) -> str:
    """Map a raw cell/label string to its output value."""
    return "" if text in PLACEHOLDERS else text


def number(text: str) -> float | None:
    """Parse a display string as a number, or return ``None``."""
    stripped = text.replace("$", "").replace("%", "").replace(",", "")
    stripped = stripped.replace("*", "").replace("\u2212", "-").strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


class _TableExtractor(HTMLParser):
    """Collect the header and body cell texts of the first table in a document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.header_rows: list[list[str]] = []
        self.body_rows: list[list[str]] = []
        self._table_depth = 0
        self._section: str | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._muted = 0

    # -- internals ---------------------------------------------------------- #
    def _flush_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            # Header labels are composed from several spans that CSS spaces
            # apart, so their text nodes must be joined with a space.  Body
            # cells are atomic display strings ("1,668", "$7.63") and must not
            # gain spaces between their text nodes.
            separator = " " if self._section == "thead" else ""
            self._row.append(_collapse(separator.join(self._cell)))
        self._cell = None

    def _flush_row(self) -> None:
        if self._row is None:
            return
        cells, self._row = self._row, None
        if not cells:
            return
        if self._section == "thead":
            self.header_rows.append(cells)
        elif self._section == "tbody":
            self.body_rows.append(cells)

    # -- HTMLParser hooks --------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
        elif self._table_depth == 1:
            if tag in ("thead", "tbody", "tfoot"):
                self._section = tag
            elif tag == "tr":
                self._flush_row()
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._flush_cell()
                self._cell = []
        if tag in ("svg", "script", "style"):
            self._muted += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("svg", "script", "style"):
            self._muted = max(0, self._muted - 1)
        if tag in ("td", "th"):
            self._flush_cell()
        elif tag == "tr":
            self._flush_cell()
            self._flush_row()
        elif tag in ("thead", "tbody", "tfoot"):
            self._flush_cell()
            self._flush_row()
            self._section = None
        elif tag == "table":
            self._flush_cell()
            self._flush_row()
            self._table_depth = max(0, self._table_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._muted == 0 and self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:  # noqa: D102 - HTMLParser API
        super().close()
        self._flush_cell()
        self._flush_row()


def parse_table(document: str) -> tuple[list[str], list[list[str]]]:
    """Return ``(leaf_header_cells, body_rows)`` for the leaderboard table."""
    extractor = _TableExtractor()
    extractor.feed(document)
    extractor.close()
    if not extractor.header_rows:
        raise TableError("no <thead> row found - is this the leaderboard page?")
    if not extractor.body_rows:
        raise TableError("no <tbody> row found - is this the leaderboard page?")
    leaf_headers = max(extractor.header_rows, key=len)
    return leaf_headers, extractor.body_rows


# --------------------------------------------------------------------------- #
# Column validation
# --------------------------------------------------------------------------- #

def validate_columns(leaf_headers: list[str]) -> list[str]:
    """Return a list of human-readable column-contract violations (empty = ok)."""
    expected_cells = len(COLUMN_PLAN) + 1  # metrics + trailing links column
    if len(leaf_headers) != expected_cells:
        return [
            f"expected {expected_cells} header cells "
            f"({len(COLUMN_PLAN)} metrics + {TRAILING_HEADER!r}), found {len(leaf_headers)}. "
            "If the page was fetched without clicking 'Expand columns', "
            "re-capture it with refresh_aa_data.py."
        ]
    issues: list[str] = []
    for index, path, kind, anchor, _shape in COLUMN_PLAN:
        actual = leaf_headers[index]
        if kind == "exact":
            ok = actual == anchor
        elif kind == "prefix":
            ok = actual.startswith(anchor)
        else:
            ok = anchor in actual
        if not ok:
            issues.append(
                f"column {index} ({'.'.join(path)}): expected {kind} anchor "
                f"{anchor!r}, found {actual!r}"
            )
    if leaf_headers[-1] != TRAILING_HEADER:
        issues.append(
            f"column {len(leaf_headers) - 1}: expected trailing header "
            f"{TRAILING_HEADER!r}, found {leaf_headers[-1]!r}"
        )
    return issues


def validate_cells(
    body_rows: list[list[str]],
    *,
    check_invariants: bool = True,
    max_reports: int = 10,
) -> tuple[list[str], int]:
    """Check every cell against its shape and every row against the invariants.

    Returns ``(violation messages, number of cells checked)``; the message list is
    capped at ``max_reports`` entries but the count of cells checked covers all rows.
    """
    problems: list[str] = []
    checked = 0
    expected_cells = len(COLUMN_PLAN) + 1

    for row_number, cells in enumerate(body_rows, start=1):
        if len(cells) != expected_cells:
            # A malformed row is reported by build_records (exit code 3); do not
            # index into it here.
            continue
        for index, path, _kind, _anchor, shape in COLUMN_PLAN:
            value = to_value(cells[index])
            checked += 1
            if value and not SHAPES[shape].fullmatch(value):
                if len(problems) < max_reports:
                    problems.append(
                        f"row {row_number} column {index} ({'.'.join(path)}): "
                        f"value {value!r} does not match shape {shape!r}"
                    )

    if not check_invariants:
        return problems, checked

    previous_sort: float | None = None
    for row_number, cells in enumerate(body_rows, start=1):
        if len(cells) != expected_cells:
            continue

        def value_of(path: tuple[str, str]) -> float | None:
            return number(to_value(cells[INDEX_BY_PATH[path]]))

        for chain, message in ROW_INVARIANTS:
            values = [value_of(path) for path in chain]
            if any(value is None for value in values):
                continue
            if any(values[i] > values[i + 1] + _EPSILON for i in range(len(values) - 1)):
                if len(problems) < max_reports:
                    problems.append(
                        f"row {row_number} ({to_value(cells[0])!r}): {message} "
                        f"- got {values}"
                    )

        current = value_of(SORT_PATH)
        if current is not None and previous_sort is not None and current > previous_sort + _EPSILON:
            if len(problems) < max_reports:
                problems.append(
                    f"row {row_number} ({to_value(cells[0])!r}): rows must be sorted by "
                    f"{'.'.join(SORT_PATH)} descending - {current} follows {previous_sort}"
                )
        if current is not None:
            previous_sort = current

    return problems, checked


# --------------------------------------------------------------------------- #
# Record building
# --------------------------------------------------------------------------- #

def assemble(values: dict[tuple[str, ...], str]) -> dict:
    """Nest the flat column values into the target schema, preserving key order."""
    record: dict = {}
    for group in TOP_LEVEL_ORDER:
        if group == "Model":
            record[group] = values[("Model",)]
            continue
        record[group] = {
            path[1]: value
            for path, value in values.items()
            if len(path) == 2 and path[0] == group
        }
    return record


def build_records(
    leaf_headers: list[str], body_rows: list[list[str]], limit: int | None = None,
) -> tuple[list[dict], list[str], dict[int, int]]:
    """Build records plus ``(warnings, placeholder counts per column)``."""
    records: list[dict] = []
    warnings: list[str] = []
    placeholder_counts: dict[int, int] = {}
    seen: dict[str, int] = {}

    for row_number, cells in enumerate(body_rows, start=1):
        if len(cells) != len(leaf_headers):
            raise RowError(
                f"row {row_number}: expected {len(leaf_headers)} cells, "
                f"found {len(cells)} (first cell: "
                f"{cells[0] if cells else '<empty row>'!r})"
            )
        values: dict[tuple[str, ...], str] = {}
        for index, path, _kind, _anchor, _shape in COLUMN_PLAN:
            raw = cells[index]
            if raw in PLACEHOLDERS:
                placeholder_counts[index] = placeholder_counts.get(index, 0) + 1
            values[path] = to_value(raw)
        record = assemble(values)
        name = record["Model"]
        if not name:
            warnings.append(f"row {row_number}: empty model name, skipped")
            continue
        if name in seen:
            warnings.append(
                f"row {row_number}: duplicate model name {name!r} "
                f"(first seen in row {seen[name]})"
            )
        else:
            seen[name] = row_number
        records.append(record)
        if limit is not None and len(records) >= limit:
            break
    return records, warnings, placeholder_counts


# --------------------------------------------------------------------------- #
# Module note
# --------------------------------------------------------------------------- #
# This module is the column contract and the extraction engine; it has no CLI of its
# own on purpose.  Run everything through ``export_aa_leaderboard.py``, which imports
# the functions below:
#
#   python scripts/export_aa_leaderboard.py                      # fetch fresh data
#   python scripts/export_aa_leaderboard.py --offline            # parse a snapshot
#   python scripts/export_aa_leaderboard.py --dry-run            # validate only
#
# Its exit codes (shared by every entry point): 0 success, 2 header/anchor contract
# violation, 3 malformed data row, 4 input missing/unreadable, 5 cell shape or row
# invariant violation, 6 Playwright missing, 7 browser capture failure.
