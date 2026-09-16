---
name: aa-llm-leaderboard
description: Fetch, refresh and export Artificial Analysis LLM leaderboard metrics into the 42-metric JSON structure (Model, Features, Intelligence, Price, Speed, Latency, End-To-End Response Time) with a single command that captures the live leaderboard in a browser, validates it, parses it, and reports what changed. Use when asked to grab or update AI model evaluation data - intelligence and benchmark scores, pricing, output speed percentiles, latency percentiles, response time - from an artificialanalysis.ai leaderboard page, or to parse a leaderboard HTML snapshot that the user already has.
whenToUse: The user wants current Artificial Analysis leaderboard data, wants an existing export refreshed, or hands over a leaderboard HTML file to parse.
---

# Artificial Analysis leaderboard export

## Summary

One command captures the live Artificial Analysis leaderboard in a browser, validates
it against a three-layer contract, parses it into 42 metrics per model, and reports
what changed since the last run:

```bash
python .dsh/skills/aa-llm-leaderboard/scripts/export_aa_leaderboard.py
```

Run it on a clean directory, run it again later to refresh, or delete the data files
in between - all three behave the same, because everything it needs is recreated from
the network. The default action is **always fetch fresh data**; parsing a snapshot
already on disk is the opt-in (`--offline`).

Never eyeball the HTML or the embedded React payload yourself: the snapshot is ~5 MB
with a few hundred rows x 43 cells, so extraction must run through the script. Row
counts and metric values drift between captures; the 42-column structure, the cell
shapes, and the ordering invariants do not.

## Inputs and outputs

- Input: none required. `--offline` instead reads a snapshot you already have
  (typically `.aa/aa_data.html`).
- Outputs: both live in the **`.aa/` folder** of the invocation directory (created on
  demand, so the project root keeps exactly one new folder), and both are overwritten
  on every run:
  - `.aa/aa_data.html` - the captured snapshot with the table expanded (43 header
    cells: 42 metrics + a trailing links column).
  - `.aa/aa_models.json` - one record per model, in page order, with these keys:
    `Model`; `Features{Context Window, Creator, License}`; `Intelligence{...20
    benchmark metrics...}`; `Price{Cost Per Task, Input Price, Output Price,
    Cache Hit Price, Cache Write Price}`; `Speed{Median, P5, P25, P75, P95}`;
    `Latency{Latency, First Answer, P5, P25, P75, P95}`;
    `End-To-End Response Time{Total, Reasoning}`.
  - `--html` / `--json` override those two paths when you need a different location.
- No fixture file is involved: validation is self-contained (anchors + shapes +
  invariants), so nothing has to be re-baselined when the site's data drifts.
- A run writes **exactly these two files and nothing else** - not in the project root
  and not in the skill directory (Python's `scripts/__pycache__` is suppressed and
  cleaned up for you). Any verification harness, scratch fixture, or helper you write
  while checking the result belongs in a scratch directory (for example `.verify/`)
  that you delete before reporting, so the project is left holding only `.aa/`.

## Workflow

1. **Default - fetch or refresh** (also the right move when the data files are
   missing or the export looks stale):

   ```bash
   python .dsh/skills/aa-llm-leaderboard/scripts/export_aa_leaderboard.py
   ```

   It prints the run summary plus a `change:` block comparing the new export with the
   previous `.aa/aa_models.json`, for example `records 307 -> 307`,
   `added=1 removed=0 changed_records=189`, `top changes: ...`. A capture that fails
   the contract is never written, so a good snapshot on disk is not clobbered by a bad
   capture.

2. **Parse a snapshot without touching the network** - use when the user hands over an
   HTML file, or when the browser is unavailable:

   ```bash
   python .dsh/skills/aa-llm-leaderboard/scripts/export_aa_leaderboard.py \
     --offline --html .aa/aa_data.html
   ```

   `--offline` still validates and still reports the change block; add `--no-diff` for
   a plain extraction. It needs no browser and no Playwright.

3. **Verify what is on disk** - the packaged form of the validation checklist below.
   It never fetches and never writes:

   ```bash
   python .dsh/skills/aa-llm-leaderboard/scripts/export_aa_leaderboard.py --verify
   ```

   It re-checks the snapshot against the contract and then reconciles **every**
   exported leaf against the raw table cell (12,894 comparisons for 307 rows),
   printing `verify: PASS snapshot=... records=<n> leaves=<n> cells=<n>
   placeholders=0 sort=ok` with exit 0, or `verify FAIL ...` with exit 5. Use this
   instead of writing a throwaway harness, and leave no verification files behind.
   An export made with `--limit` is accepted as a checked prefix of the snapshot.

4. **Validate without writing anything** - to inspect the live page (or a snapshot)
   before replacing files:

   ```bash
   python .dsh/skills/aa-llm-leaderboard/scripts/export_aa_leaderboard.py --dry-run
   ```

   It captures, validates, and prints what *would* change, but writes neither
   `.aa/aa_data.html` nor `.aa/aa_models.json`.

5. **Browser selection** - `--channel auto` (the default) tries Playwright's bundled
   chromium, then the installed Chrome, then Edge. `--channel chrome` skips the
   bundled browser entirely and is the fastest path (`pip install playwright` is the
   only installation step needed).

6. **Other flags** - `--verify` (re-check the files on disk), `--limit N` (first N
   records), `--skip-invariants` (shape checks only), `--html/--json` to rename the
   two outputs, `--timeout` for a slow page.

Only `export_aa_leaderboard.py` is a command. `scripts/parse_aa_leaderboard.py` (the
column contract and extraction engine) and `scripts/refresh_aa_data.py` (the browser
capture) are modules it imports; they have no CLI of their own on purpose.

## Column contract

Three independent layers, all in `scripts/parse_aa_leaderboard.py`:

1. **Anchors** (`COLUMN_PLAN`): every row cell index is tied to an expected header
   text (`exact` / `prefix` / `contains`), so a renamed, added, or reordered column
   fails with exit 2 instead of silently shifting values into wrong keys.
2. **Shapes** (`SHAPES`): every non-empty cell must match its column's shape; a
   decimal in a tokens-per-second column, a missing `$`, or an unnormalised
   placeholder fails with exit 5.

   | Columns | Shape |
   |---|---|
   | Context Window | `\d+(\.\d+)?[kM]` (for example `131k`, `1.05M`) |
   | Creator / Model | free text, at most 64 chars, no `$` or `%` |
   | License | `Open` or `Proprietary` |
   | Intelligence Index | `\d+\*?` (a trailing `*` marks an estimated score) |
   | other Intelligence columns | percentage, optionally signed |
   | Price columns | `$` + number (for example `$7.63`) |
   | Speed columns | integer with optional thousands separators (`1,668`) |
   | Latency / End-To-End columns | decimal seconds (`210.79`) |
   | any column | the empty string, meaning "not measured" |

3. **Invariants** (data-independent, so they survive every refresh): the export must
   be sorted by the Intelligence Index descending, speed percentiles must not
   decrease (`P5 <= P25 <= Median <= P75 <= P95`), latency percentiles must not
   decrease (`P5 <= P25 <= Latency <= P75 <= P95`), first chunk latency must not
   exceed first answer, first answer must not exceed total response time, and
   reasoning time must not exceed total response time. Violations fail with exit 5;
   `--skip-invariants` downgrades that to shape-only checking.

Mapping summary: cell 0 -> `Model`; cells 1-3 -> `Features`; cells 4-23 ->
`Intelligence`; cells 24-28 -> `Price`; cells 29-33 -> `Speed`; cells 34-39 ->
`Latency`; cells 40-41 -> `End-To-End Response Time`; cell 42 (`Further Analysis`,
the Model/Providers links) is ignored.

## Failure handling

Exit codes are shared by every script in this skill:

| Code | Meaning | Action |
|---|---|---|
| 0 | success | - |
| 2 | header/anchor contract violation (for example "found 8" = captured without expanding columns, or "column N: expected ... anchor" = the site renamed/reordered columns) | re-capture; if a column really changed, update it in `COLUMN_PLAN` |
| 3 | malformed data row (wrong cell count) | re-capture the snapshot |
| 4 | input file missing/unreadable | drop `--offline` to fetch it, or fix the path |
| 5 | cell shape or row invariant violation, or a `--verify` mismatch | the named row/column tells you which: a wrong key path in `COLUMN_PLAN`, a genuine metric-semantics change (then re-run with `--skip-invariants`), or an export that no longer matches its snapshot |
| 6 | Playwright not installed | `pip install playwright`, then `--channel chrome` |
| 7 | browser launch/navigation/capture failed | read the printed launch attempts; try `--channel chrome`/`--channel msedge`, or fall back to `--offline` on an existing snapshot |

## Known pitfalls

- The 43-column layout is **client-side state** (a TanStack "toggle all columns"
  button). It is not a query parameter and not persisted in local storage, and the
  server-rendered page always ships 8 metric columns - so a plain HTTP fetch can
  never produce an expanded snapshot.
- The page hydrates on the client: the "Expand columns" button is inert until React
  has rendered the rows, and a second click *collapses* the table again. Anything
  that automates the click must wait for the rows first and must not blindly retry.
- Values are exported **verbatim as display strings**: `"1M"`, `"984k"`, `"$7.63"`,
  `"1,668"` (thousands separator), `"63%"`, `"53"`. Do not cast them to numbers
  unless the user asks for a numeric variant.
- The placeholder `--` becomes an empty string `""`; the same is true of `-`,
  `N/A`, and empty cells.
- Real-data quirks the shapes deliberately allow: estimated Intelligence Index
  values carry a trailing `*` (`40*`), and negative indices use the Unicode minus
  sign `U+2212` (`−10`), not ASCII `-`.
- Two header cells are mojibake in some snapshots (the tau-bench Telecom/Banking
  columns). Their anchors therefore only require the ASCII fragments
  `Bench Telecom` and `Banking`; the exported key names stay clean
  (`tau^2-Bench Telecom`, `tau^3-Banking`).
- What the contract still cannot see: a key swap between two columns of the *same*
  shape inside one group (for example two integer Speed columns) passes the shape
  check and is only caught by the ordering invariants when it breaks monotonicity.
  When editing `COLUMN_PLAN`, diff the plan against the header row by hand.
- Playwright's bundled-browser download (`python -m playwright install chromium`)
  goes to `cdn.playwright.dev` and can stall for many minutes; driving the installed
  Chrome with `--channel chrome` needs no download at all and is the faster path.
- The embedded React payload in the page is *not* the export source: its field
  names, formatting, and model display names differ from the table's display
  strings.

## Validation

Run these after any change to the parser, the contract, or the capture logic:

1. `export_aa_leaderboard.py` -> exit 0, printing `shape_checked=<rows x 42>
   shape_violations=0 invariants=ok` and a `change:` block.
2. `export_aa_leaderboard.py --verify` -> exit 0 and `verify: PASS ... records=<n>
   leaves=<n> placeholders=0 sort=ok`. This is steps 2-3 below, already implemented:
   structure (record count equals the snapshot's row count, exact top-level key set,
   42 leaves per record, no placeholder left, `Model` non-empty and unique,
   Intelligence Index non-increasing) **plus a full reconciliation of every exported
   leaf against its raw table cell**. Do not re-implement it in a scratch script.
3. Only when you need something `--verify` does not cover (for example a coverage
   statistic), do a one-off check - inline (`python - <<'PY'` / a here-string piped to
   `python -`), not as a new file. Compare against the *current* cells, never against
   literals: values drift on every capture.
4. Negative paths: `--offline` with a missing snapshot exits 4; a collapsed table
   (8 header cells) exits 2; a short data row exits 3; a decimal in a Speed column,
   a swapped percentile pair, an out-of-order row, or `Reasoning` greater than
   `Total` exits 5. Every one of them must leave the files on disk untouched.

Keep every harness and scratch fixture inside one local scratch directory (for
example `.verify/`) and remove the whole directory when finished: a run must leave
only the `.aa/` folder (its two files) in the project. Do not scatter `verify_*.py`
or `summarize*.py` beside the data, and do not write to the OS temp area (the sandbox
may deny it) - use the scratch directory instead.

The run also cleans up after Python itself: `sys.dont_write_bytecode` keeps the import
of the two sibling modules from creating `scripts/__pycache__`, and an `atexit` hook
removes that directory if an earlier run (or another interpreter) left one. No
`__pycache__` appears anywhere after a run - do not add cleanup steps for it, and do
not commit one.
