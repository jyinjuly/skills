---
name: arena-llm-leaderboard
description: Fetch, refresh and export the Arena (arena.ai) agent leaderboard - every category (Overall, Code, Chat, Work, ...) - into the arena_data JSON shape with one command that drives a real browser, switches category, expands every column, saves one HTML snapshot and one JSON export per category, and reports what changed. Use when asked to grab or update Arena agent-leaderboard data (rank, net improvement, confirmed success, praise vs complaint, steerability, bash recovery, tool hallucination, sessions, cost per task percentiles, output tokens per task percentiles, price per million) or to parse an Arena leaderboard HTML snapshot that the user already has.
whenToUse: The user wants current arena.ai agent-leaderboard data, wants an existing export refreshed for one or more categories, or hands over an Arena leaderboard HTML snapshot to parse.
---

# Arena agent-leaderboard export

## Summary

One command walks every category of the Arena agent leaderboard in a real browser,
switches category, turns **every** column switch on, and writes one snapshot plus one
export per category:

```bash
python <skill-dir>/scripts/export_arena_leaderboard.py
```

`<skill-dir>` is the directory holding this `SKILL.md` (its absolute path comes back
with the loaded skill; the usual install is `~/.agents/skills/arena-llm-leaderboard`,
on Windows `C:\Users\tengeer\.agents\skills\arena-llm-leaderboard`).

Run it on a clean directory to create the artifacts, run it again later to update
them, or point `--dir` somewhere else - all three behave the same, because the
browser state (category, expanded columns) is rebuilt from the network every run.
The default action is **always fetch fresh data**; parsing snapshots already on disk
is the opt-in (`--offline`).

Never eyeball the HTML yourself: a snapshot is ~1.8 MB of minified React output with
~46 rows x 16 cells, so extraction must run through the script. Row counts and metric
values drift between captures; the column structure, the cell shapes and the ordering
invariants do not.

## Inputs and outputs

- Input: none required. `--offline` instead re-parses the `arena_*_data.html`
  snapshots already in `--dir`.
- Outputs: one pair per category, in the **`.arena/` folder** of the invocation
  directory (`--dir` overrides it; the folder is created on demand, so the project
  root keeps exactly one new folder):
  - `.arena/arena_<slug>_data.html` - the captured page with **all columns
    expanded**. The slug is the category lowercased with non-alphanumerics collapsed
    to `_`: `Overall` -> `arena_overall_data.html`, `Long Context` ->
    `arena_long_context_data.html`.
  - `.arena/arena_<slug>_data.json` - the parsed export. Exactly four top-level keys
    - `Date`, `Sessions`, `Models`, `Data` - and one record per model, in page (rank)
    order:

    ```json
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
    ```

    This is the shape of `arena_data_example.json`; the full 16-key `Data` record is
    what a fully expanded snapshot produces. Values are display strings, verbatim.
- No fixture file is involved: validation is self-contained (header anchors + cell
  shapes + rank invariants), so nothing needs re-baselining when the site's data
  drifts.
- A run writes **exactly these files and nothing else** - not in the project root and
  not in the skill directory (Python's `scripts/__pycache__` is suppressed and cleaned
  up for you). Keep any scratch harness in a directory you delete before reporting.

## Workflow

1. **First run - capture and parse every category** (also the right move when the
   artifacts are missing or stale):

   ```bash
   python <skill-dir>/scripts/export_arena_leaderboard.py
   ```

   It prints one status line per category plus a `change:` block whenever the export
   already existed, for example `[overall] source=fresh capture rows=46 columns=16
   date='Sep 16, 2026' sessions='1,850,083' models='46' cells_checked=736
   shape_violations=0 invariants=ok` followed by `[overall] change: records 46 -> 46`,
   `[overall] change: changed_records=7`, `[overall] change: top changes: ...`. A
   capture that fails the contract is never written, so a good snapshot on disk is
   not clobbered by a bad capture.

2. **Later runs - update the same artifacts.** The command is idempotent: it
   overwrites each `.arena/arena_<slug>_data.html` and `.arena/arena_<slug>_data.json`
   in place and reports the diff against the previous export. Categories are
   re-discovered from the sidebar on every run, so a newly added category produces a
   new pair and a removed one simply stops being refreshed (nothing is deleted for
   you).

3. **Re-parse without touching the network** - when the browser is unavailable or an
   existing snapshot is being re-checked:

   ```bash
   python <skill-dir>/scripts/export_arena_leaderboard.py --offline
   ```

   `--offline` reads every `arena_*_data.html` in `--dir` (default `.arena/`),
   re-parses it, rewrites the `.json` beside it and still reports the change block;
   add `--no-diff` for a plain extraction. It needs no browser and no Playwright.

4. **Verify what is on disk** - the packaged form of the validation checklist below.
   It never fetches and never writes:

   ```bash
   python <skill-dir>/scripts/export_arena_leaderboard.py --verify
   ```

   It re-parses every snapshot and reconciles the fresh parse against the `.json`
   next to it, printing `verify: PASS [overall] ... records=46 columns=16 cells=736`
   with exit 0, or `verify: FAIL ...` with exit 5. Use this instead of writing a
   throwaway harness.

5. **Validate without writing anything** - to inspect the live page before replacing
   files, or to check a capture you are unsure about:

   ```bash
   python <skill-dir>/scripts/export_arena_leaderboard.py --dry-run
   ```

6. **Scope the run and its output directory** - `--category Code` (repeatable) limits
   and orders the walk, and scopes `--offline`/`--verify` by file slug; `--dir other/`
   writes somewhere other than `.arena/`; `--limit N` exports the first N models of
   each category; `--allow-partial` accepts a snapshot that was captured without every
   column expanded (the missing keys are omitted instead of failing with exit 2);
   `--skip-invariants` downgrades the rank checks; `--timeout` raises the wait for a
   slow page.

7. **Browser selection** - `--channel auto` (the default) tries Playwright's bundled
   chromium, then the installed Chrome, then Edge. `--channel chrome` skips the
   bundled browser entirely and is the fastest path (`pip install playwright` is the
   only installation step needed).

Only `export_arena_leaderboard.py` is a command. `scripts/parse_arena_leaderboard.py`
(the column contract and extraction engine) and `scripts/refresh_arena_data.py` (the
browser capture) are modules it imports; they have no CLI of their own on purpose.

## Column contract

Three independent layers, all in `scripts/parse_arena_leaderboard.py`:

1. **Anchors** (`COLUMN_PLAN`): every output key is tied to an expected **header
   text** (`exact` / `prefix` / `contains`), never to a cell position, so the export
   is independent of column order and of which optional columns the panel expanded. A
   renamed, added or missing column fails with exit 2 instead of silently shifting
   values into wrong keys.

   | Column | Key | Kind |
   |---|---|---|
   | `Rank` | `Rank{Num,Min,Max}` | rank cell (`1` over `1 <-> 2`) |
   | `Model` | `Model{Name,ProviderAndLicense}` | model cell (name + `Provider \u00b7 License`) |
   | `Net Improvement`, `Confirmed Success`, `Praise vs Complaint`, `Steerability`, `Bash Recovery`, `Tool Hallucination` | `{Value,Range}` | score cell (`13.71%` + `\u00b11.72%`) |
   | `Sessions` | plain string | `13,320` |
   | `Cost/Task (P25\|P50\|P95)` | plain string | `$0.94` |
   | `Output Tokens/Task (P25\|P50\|P95)` | plain string | `10K`, `516.6K` |
   | `Price $/M` | plain string | `$10 / $50` |

2. **Shapes** (`SHAPES`): every non-empty cell must match its column's shape; a
   decimal in the rank cell, a score without `%`, a missing `$` fails with exit 5.
   `N/A`, `--`, `-`, `\u2014` and the empty string are accepted as "not measured" and are
   exported verbatim.

3. **Invariants** (`_check_invariants`): data-independent, so they survive every
   refresh - `Rank.Min <= Rank.Num <= Rank.Max`, the exported rows are in ascending
   rank order, `Model.Name` is never empty, and model names are unique. Violations
   fail with exit 5; `--skip-invariants` downgrades that to shape-only checking. The
   page's own `"<n> models"` counter is reconciled against the row count as well, so a
   paginated or stale table cannot pass as a full roster (`--limit` skips that check).

The one derived value is the **sign of a score**: the site renders every score as an
absolute percentage plus a colour, so green becomes `+` and red becomes `-`
(`13.71%` -> `"+13.71%"`, `0.47%` -> `"-0.47%"`). The arrow direction is *not* the
sign: for `Tool Hallucination` a falling value is an improvement, so it is green
while its arrow points down, and the schema keeps `"+0.37%"`.

## Failure handling

Exit codes are shared by every script in this skill:

| Code | Meaning | Action |
|---|---|---|
| 0 | success | - |
| 2 | header/anchor contract violation, or the snapshot was captured without every column expanded ("missing 'Cost/Task (P25)' ...") | re-capture; if a column really changed, update `COLUMN_PLAN`; use `--allow-partial` to export a legitimately partial snapshot |
| 3 | malformed data row (fewer cells than the header) | re-capture the snapshot |
| 4 | input file/directory missing or unreadable | drop `--offline` to fetch, or fix `--dir` |
| 5 | cell shape or row invariant violation, or a `--verify` mismatch | the named row/column tells you which: a wrong key in `COLUMN_PLAN`, a genuine metric-semantics change (then re-run with `--skip-invariants`), or an export that no longer matches its snapshot |
| 6 | Playwright not installed | `pip install playwright`, then `--channel chrome` |
| 7 | browser launch/navigation/capture failure, including a Cloudflare bot-check page ("HTTP 429: arena.ai returned a bot-check page") | read the printed launch attempts and the `[category] ...` step that failed; on a bot-check, wait a few minutes before retrying (never in a loop), then try `--channel chrome`/`--channel msedge`, or fall back to `--offline` on an existing snapshot |

When a capture fails, or the contract rejects it, **nothing is written** for that run:
the previous artifacts stay as they are.

## Known pitfalls

- The expanded table is **client-side React state** (the `Edit columns` panel). It is
  not a query parameter and not persisted in local storage, and the server-rendered
  page ships a default column set, so a plain HTTP fetch can never produce a full
  snapshot. Always drive a browser.
- The switches report **both** `aria-checked="true|false"` and
  `data-state="checked|unchecked"`. Read `data-state` (or treat `"true"` as checked);
  comparing `aria-checked` against the literal `"checked"` never matches and turns
  the "turn every switch on" loop into an endless click on the first switch.
- `button[aria-label="Edit columns"]` matches **two** elements (a responsive
  duplicate). Always filter with `:visible`.
- Switching category is client-side navigation: the URL updates about a second after
  the click, and the table re-renders. Wait for the heading badge *and* the rows, and
  re-check the badge before writing the file - the scraper aborts if the captured page
  shows a different category than the filename claims.
- The client-side router **keeps the previous categories mounted**: after switching
  twice, `page.content()` holds two or three whole copies of the page (the live one
  first, then the stale ones), so a naive capture grows from 1.8 MB to 4.3 MB and a
  position-based reader could pick the wrong table. The scraper reloads the category's
  own URL before expanding columns and refuses to return a snapshot with more than one
  `<table>`; if you script this by hand, reload before capturing.
- Capture with the column panel **closed** (the scraper presses `Escape` first). An
  open panel adds its own table/preview markup to the DOM, and a snapshot that is
  supposed to be "the leaderboard page" should not carry a modal overlay.
- arena.ai is behind **Cloudflare**. Several captures in a row (or any retry loop) get
  an HTTP 429 bot-check page instead of the leaderboard; the capture then fails with
  exit 7 and says so explicitly. Wait a few minutes between browser refreshes, and use
  `--offline` to re-parse what you already have instead of hammering the site.
- The panel scrolls (`max-height: 400px`), so the lower switches (Output Tokens/Task
  P95, Price $/M) are off-screen; scroll each switch into view before clicking.
- Values are exported **verbatim as display strings**: `"53.2K"`, `"$10 / $50"`,
  `"1,850,083"`, and `"N/A"` for a model without a published price. Do not cast them
  to numbers unless the user asks for a numeric variant.
- `Rank` is three numbers in one cell: the rank, then the interval it moved within
  (`1 <-> 2` in the UI becomes `Min`/`Max`). The arrow glyph between them is an SVG,
  so the cell text is just `1 1 2`.
- The table has no pagination: every model of the category is in the `<tbody>` (46 for
  a full roster). If a category ever paginates, the row count silently drops - compare
  it with the page's own `"<n> models"` counter.
- Two header cells can look identical after tag-stripping if the site adds a tooltip
  glyph; the contract reports such an ambiguous header instead of guessing.
- Playwright's bundled-browser download (`python -m playwright install chromium`) goes
  to `cdn.playwright.dev` and can stall; driving the installed Chrome with
  `--channel chrome` needs no download at all.
- Each snapshot is ~1.8 MB (about 7 MB for four categories). That is expected - do not
  "clean up" the `.html` files, they are the audit trail for the `.json` beside them.

## Validation

Run these after any change to the parser, the contract, or the capture logic:

1. `export_arena_leaderboard.py` -> exit 0, with a `[...]` status line per category
   (`shape_violations=0 invariants=ok`) and a `change:` block on re-runs.
2. `export_arena_leaderboard.py --verify` -> exit 0 and one `verify: PASS [<category>]
   ...` line per pair. This is step 3 below, already implemented: structure (exact
   top-level key set, exact per-record key set, non-empty and unique model names,
   ascending rank) **plus a full reconciliation of the fresh parse against the
   `.json`**. Do not re-implement it in a scratch script.
3. Only when you need something `--verify` does not cover (for example a coverage
   statistic), do a one-off check - inline (`python - <<'PY'` / a here-string piped to
   `python -`), not as a new file, and compare against the *current* cells, never
   against literals: values drift on every capture.
4. Negative paths: `--offline` with no snapshot in `--dir` exits 4; a snapshot captured
   with 12 of 16 columns exits 2 (unless `--allow-partial`); a truncated row exits 3;
   a score without `%`, a rank cell that is not three numbers, or a decreasing rank
   exits 5. Every one of them must leave the artifacts on disk untouched.
5. A run leaves only the `.arena/` folder (its `arena_*_data.html` /
   `arena_*_data.json` pairs) in the project - nothing in the project root, no
   `__pycache__`, no `.tmp` files.
