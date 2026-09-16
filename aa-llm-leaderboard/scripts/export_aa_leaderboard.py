#!/usr/bin/env python3
"""Fetch fresh Artificial Analysis leaderboard data in one command.

The default action is *refresh*: open a browser, expand the leaderboard columns,
validate the whole contract, save the snapshot, parse it, and report what changed
since the previous export.  Running this on a clean directory works the same way as
running it on top of existing files - everything it needs is recreated.

Both outputs live in ``.aa/`` next to the invocation directory, so the project root
keeps only that one folder (``--html``/``--json`` still override the names).

Usage::

    python export_aa_leaderboard.py                     # refresh + parse
    python export_aa_leaderboard.py --channel chrome     # reuse the installed Chrome
    python export_aa_leaderboard.py --offline            # parse an existing snapshot
    python export_aa_leaderboard.py --verify             # re-check the files on disk
    python export_aa_leaderboard.py --dry-run            # validate + preview, write nothing

``--verify`` never fetches and never writes: it re-checks the snapshot against the
column contract and then reconciles **every** exported leaf against the raw table
cell, so a reviewer does not have to write an ad-hoc harness.  Exit code 0 means both
files agree; 5 means they do not.

Shared exit codes (identical across this skill's scripts)::

    0  success
    2  header/anchor contract violation
    3  malformed data row
    4  input file missing or unreadable
    5  cell shape or row invariant violation, or a --verify mismatch
    6  Playwright is not installed (install it, or use --offline)
    7  browser launch/navigation/capture failure (try --channel chrome)
"""

from __future__ import annotations

import argparse
import atexit
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

# The two modules below live next to this file; importing them must not litter the
# skill tree with a __pycache__ directory.
sys.dont_write_bytecode = True

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_SCRIPTS_DIR))
import parse_aa_leaderboard as aa  # noqa: E402
import refresh_aa_data as refresh  # noqa: E402


def _remove_bytecode_cache() -> None:
    """Drop this skill's __pycache__ (from an earlier run or another interpreter)."""
    cache = SKILL_SCRIPTS_DIR / "__pycache__"
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)


# A run must leave only its two outputs behind, including inside the skill directory.
atexit.register(_remove_bytecode_cache)

DEFAULT_HTML = ".aa/aa_data.html"
DEFAULT_JSON = ".aa/aa_models.json"


def _log(message: str) -> None:
    print(f"export_aa_leaderboard: {message}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--html", default=DEFAULT_HTML, help="snapshot file to write/read")
    parser.add_argument("--json", default=DEFAULT_JSON, help="export file to write")
    parser.add_argument("--url", default=refresh.DEFAULT_URL, help="leaderboard URL")
    parser.add_argument("--channel", default="auto",
                        choices=("auto", "chromium", "chrome", "msedge"),
                        help="browser to drive (see refresh_aa_data.py)")
    parser.add_argument("--timeout", type=float, default=30_000,
                        help="milliseconds to wait for the expanded table")
    parser.add_argument("--offline", action="store_true",
                        help="do not open a browser; parse the existing --html snapshot")
    parser.add_argument("--dry-run", action="store_true",
                        help="capture and validate, then report what would change, "
                             "but write neither the snapshot nor the export")
    parser.add_argument("--limit", type=int, default=None,
                        help="only export the first N records")
    parser.add_argument("--skip-invariants", action="store_true",
                        help="only check cell shapes, not the cross-field invariants")
    parser.add_argument("--verify", action="store_true",
                        help="do not fetch or write anything: re-check the existing "
                             "snapshot and export (structure + full cell reconciliation)")
    parser.add_argument("--no-diff", action="store_true",
                        help="do not report changes against the previous export")
    return parser.parse_args(argv)


def summarise_changes(previous: list[dict], current: list[dict], top: int = 5) -> list[str]:
    """Describe how ``current`` differs from ``previous``."""
    previous_by_name = {record["Model"]: record for record in previous}
    current_names = {record["Model"] for record in current}
    added = [record["Model"] for record in current if record["Model"] not in previous_by_name]
    removed = [name for name in previous_by_name if name not in current_names]

    changes: Counter[str] = Counter()
    changed_records = 0
    for record in current:
        old = previous_by_name.get(record["Model"])
        if old is None:
            continue
        touched = False
        for group, values in record.items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                if old.get(group, {}).get(key) != value:
                    changes[f"{group}.{key}"] += 1
                    touched = True
        if touched:
            changed_records += 1

    lines = [
        f"records {len(previous)} -> {len(current)}",
        f"added={len(added)} removed={len(removed)} changed_records={changed_records}",
    ]
    if added:
        lines.append("added: " + ", ".join(added[:10]) + (" ..." if len(added) > 10 else ""))
    if removed:
        lines.append("removed: " + ", ".join(removed[:10]) + (" ..." if len(removed) > 10 else ""))
    if changes:
        lines.append("top changes: " + ", ".join(
            f"{field} x{count}" for field, count in changes.most_common(top)
        ))
    return lines


# --------------------------------------------------------------------------- #
# --verify: re-check the files on disk without fetching or writing anything
# --------------------------------------------------------------------------- #

#: Every leaf path the schema promises, straight from the column contract.
EXPECTED_LEAVES: tuple[tuple[str, ...], ...] = tuple(
    entry[1] for entry in aa.COLUMN_PLAN if len(entry[1]) == 2
)

#: Values that must never survive into the export (they mean "not measured").
LEFTOVER_PLACEHOLDERS = ("--", "-", "N/A")


def _walk_leaves(record: dict):
    """Yield ``(group, key, value)`` for every metric leaf of a record."""
    for group, values in record.items():
        if isinstance(values, dict):
            for key, value in values.items():
                yield group, key, value


def verify_files(html_path: Path, json_path: Path, skip_invariants: bool = False) -> int:
    """Check the snapshot and the export on disk against the whole contract.

    Returns 0 when everything matches, otherwise the shared failure code (2 for a
    header/anchor violation, 4 for a missing file, 5 for any other mismatch).
    """
    for path in (html_path, json_path):
        if not path.is_file():
            _log(f"cannot verify: {path} not found")
            return 4

    try:
        document = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        _log(f"cannot read {html_path}: {error}")
        return 4
    try:
        records = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _log(f"cannot read {json_path}: {error}")
        return 4
    if not isinstance(records, list):
        _log(f"{json_path}: expected a JSON array of records")
        return 5

    try:
        leaf_headers, rows, checked, anchor_issues, problems = \
            refresh.validate_snapshot(document, check_invariants=not skip_invariants)
    except aa.TableError as error:
        _log(f"{html_path}: {error}")
        return 2
    if anchor_issues:
        for issue in anchor_issues:
            _log(f"verify FAIL (snapshot): {issue}")
        return 2
    if problems:
        for problem in problems[:10]:
            _log(f"verify FAIL (snapshot): {problem}")
        return 5

    failures: list[str] = []

    # Structure: exact key set, leaf count, model names.
    expected_top = set(aa.TOP_LEVEL_ORDER)
    expected_leaves = set(EXPECTED_LEAVES)
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            failures.append(f"record {position}: not a JSON object")
            continue
        if set(record) != expected_top:
            failures.append(
                f"record {position}: top-level keys {sorted(record)} != "
                f"{sorted(expected_top)}"
            )
            continue
        if not isinstance(record.get("Model"), str) or not record["Model"]:
            failures.append(f"record {position}: empty Model")
        for group in aa.TOP_LEVEL_ORDER:
            if group == "Model":
                continue
            if not isinstance(record[group], dict):
                failures.append(f"record {position}: {group} is not an object")
                continue
            wanted = {path[1] for path in expected_leaves if path[0] == group}
            if set(record[group]) != wanted:
                failures.append(
                    f"record {position}: {group} keys {sorted(record[group])} != "
                    f"{sorted(wanted)}"
                )
        for group, key, value in _walk_leaves(record):
            if isinstance(value, str) and value.strip() in LEFTOVER_PLACEHOLDERS:
                failures.append(
                    f"record {position}: {group}.{key} still holds the placeholder "
                    f"{value!r}"
                )
        if len(failures) >= 10:
            break

    if not failures:
        names = [record["Model"] for record in records]
        if len(set(names)) != len(names):
            failures.append("duplicate Model values in the export")
        # An export made with --limit is a legitimate prefix of the snapshot.
        limited = len(records) < len(rows)
        if limited:
            _log(f"note: export has {len(records)} records, snapshot has {len(rows)} "
                 f"rows - treating it as a --limit export and checking the prefix")
        if len(records) > len(rows):
            failures.append(
                f"export has {len(records)} records but the snapshot only has "
                f"{len(rows)} rows"
            )

    # Reconciliation: every exported leaf must equal its raw cell.
    if not failures:
        mismatches = 0
        for position, record in enumerate(records):
            row = rows[position]
            for index, path, _kind, _anchor, _shape in aa.COLUMN_PLAN:
                expected = aa.to_value(row[index])
                actual = record[path[0]] if len(path) == 1 else record[path[0]][path[1]]
                if actual != expected:
                    mismatches += 1
                    if mismatches <= 10:
                        failures.append(
                            f"row {position + 1} {'.'.join(path)}: export={actual!r} "
                            f"cell={expected!r}"
                        )
        leaves_checked = len(records) * len(aa.COLUMN_PLAN)
    else:
        leaves_checked = 0

    # Ordering invariant on the exported values themselves.
    if not failures:
        measured = [
            aa.number(record["Intelligence"]["Artificial Analysis Intelligence Index"])
            for record in records
        ]
        measured = [value for value in measured if value is not None]
        if any(measured[i] > measured[i - 1] + 1e-6 for i in range(1, len(measured))):
            failures.append("Intelligence Index is not non-increasing in the export")

    for failure in failures[:10]:
        _log(f"verify FAIL: {failure}")
    if failures:
        _log(f"verify: FAIL ({len(failures)} problem(s) reported, first 10 shown)")
        return 5

    _log(f"verify: PASS snapshot={html_path} export={json_path} "
         f"records={len(records)} leaves={leaves_checked} "
         f"cells={checked} placeholders=0 sort=ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - exotic stdio
            pass

    args = parse_args(argv)
    html_path = Path(args.html)
    json_path = Path(args.json)

    if args.verify:
        return verify_files(html_path, json_path, args.skip_invariants)

    if args.offline:
        if not html_path.is_file():
            _log(f"input HTML not found: {html_path} (drop --offline to fetch it)")
            return 4
        try:
            document = html_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            _log(f"cannot read {html_path}: {error}")
            return 4
        source = f"existing snapshot {html_path}"
    else:
        try:
            document = refresh.capture_document(args.url, args.channel, args.timeout, _log)
        except refresh.PlaywrightMissing as error:
            _log(str(error))
            if html_path.is_file():
                _log(f"hint: {html_path} exists - rerun with --offline to parse it")
            return 6
        except refresh.CaptureError as error:
            _log(f"browser step failed: {error}")
            if html_path.is_file():
                _log(f"hint: {html_path} exists - rerun with --offline to parse it")
            return 7
        warning = refresh.translation_warning(document)
        if warning:
            _log(f"warning: {warning}")
        source = "fresh capture"

    try:
        leaf_headers, body_rows, checked, anchor_issues, problems = \
            refresh.validate_snapshot(document, check_invariants=not args.skip_invariants)
    except aa.TableError as error:
        _log(str(error))
        return 2

    if anchor_issues:
        for issue in anchor_issues:
            _log(issue)
        if not args.offline:
            _log(f"{html_path} NOT written")
        return 2

    if problems:
        for problem in problems:
            _log(problem)
        if not args.offline:
            _log(f"{html_path} NOT written")
        return 5

    if not args.offline and not args.dry_run:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = html_path.with_suffix(html_path.suffix + ".tmp")
        temp_path.write_text(document, encoding="utf-8")
        temp_path.replace(html_path)
        _log(f"wrote {html_path} (header_cells={len(leaf_headers)} "
             f"rows={len(body_rows)} shape_checked={checked})")

    try:
        records, warnings, placeholder_counts = aa.build_records(
            leaf_headers, body_rows, args.limit,
        )
    except aa.RowError as error:
        _log(str(error))
        return 3

    previous: list[dict] | None = None
    if not args.no_diff and json_path.is_file():
        try:
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                previous = loaded
        except (OSError, json.JSONDecodeError) as error:
            _log(f"note: previous export unreadable, skipping the comparison ({error})")

    placeholders = ", ".join(
        f"col{index}={count}" for index, count in sorted(placeholder_counts.items())
    ) or "none"
    _log(f"source={source} records={len(records)} mapped={len(aa.COLUMN_PLAN)} "
         f"shape_checked={checked} shape_violations=0 "
         f"invariants={'skipped' if args.skip_invariants else 'ok'} "
         f"placeholders_1={placeholders}")
    if args.dry_run:
        _log(f"dry run: neither {html_path} nor {json_path} written")
    else:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=4) + "\n", encoding="utf-8",
        )
        _log(f"wrote {json_path}")
    for warning in warnings:
        _log(f"warning: {warning}")
    if previous is not None:
        for line in summarise_changes(previous, records):
            _log(f"change: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
