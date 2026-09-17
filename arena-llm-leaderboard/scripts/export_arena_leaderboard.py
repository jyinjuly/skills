#!/usr/bin/env python3
"""Fetch fresh Arena agent-leaderboard data for every category in one command.

The default action is *refresh*: open a browser, walk the sidebar's categories, switch
to each one, turn every column switch on, save one HTML snapshot per category, parse
each snapshot into the ``arena_data_example.json`` shape, and report what changed since
the previous export.  Running this on a clean directory creates the files; running it
again updates them in place.

Every category produces a pair in the output directory (``--dir``, default the
``.arena/`` folder of the invocation directory, created on demand, so the project
root keeps exactly one new folder)::

    .arena/arena_overall_data.html / .arena/arena_overall_data.json
    .arena/arena_code_data.html    / .arena/arena_code_data.json
    .arena/arena_chat_data.html    / .arena/arena_chat_data.json
    .arena/arena_work_data.html    / .arena/arena_work_data.json

Usage::

    python export_arena_leaderboard.py                  # refresh every category
    python export_arena_leaderboard.py --channel chrome # reuse the installed Chrome
    python export_arena_leaderboard.py --category Code  # one category only
    python export_arena_leaderboard.py --offline        # re-parse the snapshots on disk
    python export_arena_leaderboard.py --verify         # re-check there is nothing to do
    python export_arena_leaderboard.py --dry-run        # validate + preview, write nothing

Shared exit codes (identical across this skill's scripts)::

    0  success
    2  header/anchor contract violation (unknown column, or columns not expanded)
    3  malformed data row (a row has fewer cells than the header)
    4  input file missing or unreadable
    5  cell shape or row invariant violation, or a --verify mismatch
    6  Playwright is not installed (install it, or use --offline)
    7  browser launch/navigation/capture failure (try --channel chrome)
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

# The two modules below live next to this file; importing them must not litter the
# skill tree with a __pycache__ directory.
sys.dont_write_bytecode = True

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_SCRIPTS_DIR))
import parse_arena_leaderboard as arena  # noqa: E402
import refresh_arena_data as refresh  # noqa: E402


def _remove_bytecode_cache() -> None:
    """Drop this skill's __pycache__ (from an earlier run or another interpreter)."""
    cache = SKILL_SCRIPTS_DIR / "__pycache__"
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)


atexit.register(_remove_bytecode_cache)

DEFAULT_DIR = ".arena"
FILE_STEM = "arena_{slug}_data"


def _log(message: str) -> None:
    print(f"export_arena_leaderboard: {message}", file=sys.stderr)


def slugify(category: str) -> str:
    """``"Overall"`` -> ``overall``; ``"Long Context"`` -> ``long_context``."""
    return re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=DEFAULT_DIR,
                        help="directory holding the arena_*_data.html/json pairs "
                             "(default .arena, created on demand)")
    parser.add_argument("--category", action="append", default=None,
                        help="only this category (repeatable); default is every "
                             "category the sidebar lists. Scopes --offline and "
                             "--verify too, by file slug")
    parser.add_argument("--url", default=refresh.DEFAULT_URL, help="leaderboard URL")
    parser.add_argument("--channel", default="auto",
                        choices=("auto", "chromium", "chrome", "msedge"),
                        help="browser to drive (see refresh_arena_data.py)")
    parser.add_argument("--timeout", type=float, default=30_000,
                        help="milliseconds to wait for a table, a panel or a switch")
    parser.add_argument("--offline", action="store_true",
                        help="do not open a browser; re-parse the arena_*_data.html "
                             "snapshots already in --dir")
    parser.add_argument("--dry-run", action="store_true",
                        help="capture and validate, then report what would change, "
                             "but write nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="only export the first N records of every category")
    parser.add_argument("--skip-invariants", action="store_true",
                        help="only check cell shapes, not the cross-field invariants")
    parser.add_argument("--allow-partial", action="store_true",
                        help="accept a snapshot captured without every column "
                             "expanded (the missing keys are omitted)")
    parser.add_argument("--verify", action="store_true",
                        help="do not fetch and do not write: re-parse every snapshot "
                             "and reconcile it against the export next to it")
    parser.add_argument("--no-diff", action="store_true",
                        help="do not report changes against the previous export")
    return parser.parse_args(argv)


def pair_paths(out_dir: Path, category: str) -> tuple[Path, Path]:
    stem = FILE_STEM.format(slug=slugify(category))
    return out_dir / f"{stem}.html", out_dir / f"{stem}.json"


def slug_of_path(html_path: Path) -> str:
    """``arena_overall_data.html`` -> ``overall``."""
    stem = html_path.stem
    return stem[len("arena_"):-len("_data")] if stem.startswith("arena_") else stem


def discover_pairs(
    out_dir: Path, categories: list[str] | None = None,
) -> list[tuple[str, Path, Path]]:
    """Return ``(category, html, json)`` for every snapshot already on disk.

    ``categories`` limits the result to those slugs, for symmetry with the browser
    walk's ``--category``.
    """
    wanted = {slugify(name) for name in categories} if categories else None
    pairs: list[tuple[str, Path, Path]] = []
    for html_path in sorted(out_dir.glob(f"{FILE_STEM.format(slug='*')}.html")):
        if wanted is not None and slug_of_path(html_path) not in wanted:
            continue
        try:
            document = html_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            _log(f"cannot read {html_path}: {error}")
            continue
        category = refresh.category_badge(document) or html_path.stem
        pairs.append((category, html_path, html_path.with_suffix(".json")))
    return pairs


def summarise_changes(previous: dict, current: dict, top: int = 5) -> list[str]:
    """Describe how the freshly parsed document differs from the previous export."""
    lines: list[str] = []
    for key in ("Date", "Sessions", "Models"):
        if previous.get(key) != current.get(key):
            lines.append(f"{key}: {previous.get(key)!r} -> {current.get(key)!r}")

    old_rows = [row for row in previous.get("Data") or [] if isinstance(row, dict)]
    new_rows = [row for row in current.get("Data") or [] if isinstance(row, dict)]

    def name_of(row: dict) -> str:
        model = row.get("Model")
        return model.get("Name", "") if isinstance(model, dict) else ""

    old_by_name = {name_of(row): row for row in old_rows if name_of(row)}
    new_names = {name_of(row) for row in new_rows if name_of(row)}
    added = [name for name in new_names if name not in old_by_name]
    removed = [name for name in old_by_name if name not in new_names]

    changes: Counter[str] = Counter()
    changed_records = 0
    for row in new_rows:
        old = old_by_name.get(name_of(row))
        if old is None:
            continue
        touched = False
        for key, value in row.items():
            if isinstance(value, dict):
                for leaf, leaf_value in value.items():
                    if (old.get(key) or {}).get(leaf) != leaf_value:
                        changes[f"{key}.{leaf}"] += 1
                        touched = True
            elif old.get(key) != value:
                changes[key] += 1
                touched = True
        changed_records += 1 if touched else 0

    lines.append(f"records {len(old_rows)} -> {len(new_rows)}")
    lines.append(f"added={len(added)} removed={len(removed)} "
                 f"changed_records={changed_records}")
    if added:
        lines.append("added: " + ", ".join(sorted(added)[:10])
                     + (" ..." if len(added) > 10 else ""))
    if removed:
        lines.append("removed: " + ", ".join(sorted(removed)[:10])
                     + (" ..." if len(removed) > 10 else ""))
    if changes:
        lines.append("top changes: " + ", ".join(
            f"{field} x{count}" for field, count in changes.most_common(top)))
    return lines


def parse_document(
    document: str, args: argparse.Namespace,
) -> tuple[dict, list[str], list[str], int]:
    return arena.build_payload(
        document,
        limit=args.limit,
        check_invariants=not args.skip_invariants,
        allow_partial=args.allow_partial,
    )


def report_failure(label: str, anchor_issues: list[str], problems: list[str]) -> int:
    """Print the validation report and return the shared failure code."""
    if anchor_issues:
        for issue in anchor_issues:
            _log(f"[{label}] {issue}")
        return 2
    for problem in problems[:10]:
        _log(f"[{label}] {problem}")
    if len(problems) > 10:
        _log(f"[{label}] ... {len(problems) - 10} more problem(s)")
    return 5


def verify_all(out_dir: Path, args: argparse.Namespace) -> int:
    """Re-parse every snapshot and reconcile it against the export beside it."""
    pairs = discover_pairs(out_dir, args.category)
    if not pairs:
        _log(f"nothing to verify: no {FILE_STEM.format(slug='*')}.html in "
             f"{out_dir.resolve()}")
        return 4

    failures = 0
    for category, html_path, json_path in pairs:
        if not json_path.is_file():
            _log(f"verify FAIL [{category}]: {json_path} not found")
            failures += 1
            continue
        try:
            document = html_path.read_text(encoding="utf-8", errors="replace")
            stored = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            _log(f"cannot read {html_path}/{json_path}: {error}")
            return 4

        try:
            payload, anchor_issues, problems, checked = parse_document(document, args)
        except (arena.TableError, arena.RowError) as error:
            _log(f"verify FAIL [{category}]: {error}")
            failures += 1
            continue
        if anchor_issues or problems:
            report_failure(category, anchor_issues, problems)
            failures += 1
            continue
        if stored != payload:
            failures += 1
            _log(f"verify FAIL [{category}]: {json_path.name} does not match a fresh "
                 "parse of the snapshot")
            for line in summarise_changes(stored if isinstance(stored, dict) else {},
                                          payload):
                _log(f"verify diff [{category}]: {line}")
            continue
        _log(f"verify: PASS [{category}] snapshot={html_path.name} "
             f"export={json_path.name} records={len(payload['Data'])} "
             f"columns={len(payload['Data'][0]) if payload['Data'] else 0} "
             f"cells={checked}")

    if failures:
        _log(f"verify: FAIL ({failures} of {len(pairs)} pair(s))")
        return 5
    _log(f"verify: PASS ({len(pairs)} pair(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - exotic stdio
            pass

    args = parse_args(argv)
    out_dir = Path(args.dir)

    if args.verify:
        return verify_all(out_dir, args)

    # ---- collect the snapshots (browser or disk) -------------------------- #
    if args.offline:
        if not out_dir.is_dir():
            _log(f"input directory not found: {out_dir}")
            return 4
        pairs = discover_pairs(out_dir, args.category)
        if not pairs:
            _log(f"no {FILE_STEM.format(slug='*')}.html in {out_dir} "
                 "(drop --offline to fetch them)")
            return 4
        sources = []
        for category, html_path, json_path in pairs:
            sources.append((category, html_path, json_path,
                            html_path.read_text(encoding="utf-8", errors="replace"),
                            f"existing snapshot {html_path.name}"))
    else:
        try:
            captures = refresh.capture_documents(
                url=args.url, categories=args.category, channel=args.channel,
                timeout_ms=args.timeout, log=_log,
            )
        except refresh.PlaywrightMissing as error:
            _log(str(error))
            return 6
        except refresh.CaptureError as error:
            _log(f"browser step failed: {error}")
            if discover_pairs(out_dir, args.category):
                _log("hint: snapshots are already on disk - rerun with --offline to "
                     "re-parse them")
            return 7
        sources = []
        for category, document in captures:
            html_path, json_path = pair_paths(out_dir, category)
            sources.append((category, html_path, json_path, document, "fresh capture"))

    # ---- validate and parse everything before writing anything ------------ #
    parsed: list[tuple[str, Path, Path, str, str, dict, int]] = []
    for category, html_path, json_path, document, source in sources:
        try:
            payload, anchor_issues, problems, checked = parse_document(document, args)
        except (arena.TableError, arena.RowError) as error:
            _log(f"[{category}] {error}")
            return 2 if isinstance(error, arena.TableError) else 3
        if anchor_issues or problems:
            code = report_failure(category, anchor_issues, problems)
            _log("nothing was written")
            return code
        parsed.append((category, html_path, json_path, document, source, payload,
                       checked))

    # ---- write, then report ---------------------------------------------- #
    for category, html_path, json_path, document, source, payload, checked in parsed:
        previous: dict | None = None
        if not args.no_diff and json_path.is_file():
            try:
                loaded = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous = loaded
            except (OSError, json.JSONDecodeError) as error:
                _log(f"[{category}] note: previous export unreadable, skipping the "
                     f"comparison ({error})")

        records = payload["Data"]
        columns = len(records[0]) if records else 0
        _log(f"[{category}] source={source} rows={len(records)} columns={columns} "
             f"date={payload['Date']!r} sessions={payload['Sessions']!r} "
             f"models={payload['Models']!r} cells_checked={checked} "
             f"shape_violations=0 "
             f"invariants={'skipped' if args.skip_invariants else 'ok'}")

        if args.dry_run:
            _log(f"[{category}] dry run: neither {html_path.name} nor "
                 f"{json_path.name} written")
        else:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = html_path.with_suffix(html_path.suffix + ".tmp")
            temp_path.write_text(document, encoding="utf-8")
            temp_path.replace(html_path)
            _log(f"[{category}] wrote {html_path} ({len(document):,} characters)")

            json_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = json_path.with_suffix(json_path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(json_path)
            _log(f"[{category}] wrote {json_path}")

        if previous is not None:
            for line in summarise_changes(previous, payload):
                _log(f"[{category}] change: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
