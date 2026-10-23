# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`claude-usage` is a zero-dependency Python CLI (stdlib only) that reports Claude Code token usage and cost by reading the local JSONL transcript logs in `~/.claude/projects/`.

## Running

```bash
python3 claude_usage daily                          # also: weekly, monthly
python3 claude_usage daily --since 20260701 --until 20260723
python3 claude_usage monthly --json
python3 claude_usage daily --path /some/other/projects
python3 claude_usage daily --refresh-pricing        # force pricing re-download
python3 claude_usage report                         # per-session report; also --json, --markdown
```

There is no build step, no packaging (`setup.py`/`pyproject.toml`), no test suite, and no lint config. Verify changes by running the CLI directly.

`bin/claude_usage` is a checked-in `zipapp` build of the `claude_usage/` package (see README's "Export as a zipapp" section) for running on machines without this checkout. Rebuild it after any source change: `python3 -m zipapp claude_usage -p "/usr/bin/env python3" -o bin/claude_usage`.

## Import layout — read before adding modules

The package directory is `claude_usage/`. Despite sharing its name with the directory, `claude_usage.py` inside it is **not imported as a package** — `python3 claude_usage ...` runs `claude_usage/__main__.py` with `claude_usage/` itself (not the repo root) prepended to `sys.path`. So `from claude_usage import ClaudeUsage` in `__main__.py` resolves to the sibling module `claude_usage/claude_usage.py`, not a self-import of the package.

Consequences:
- Modules use **flat imports** (`from pricing_cache import PricingCache`), never relative (`from .pricing_cache import ...`). Relative imports will break the entry point.
- `claude_usage/__init__.py` is vestigial — it is never executed under this invocation. Don't put logic there.

## Architecture

Five modules, one responsibility each:

- `__main__.py` — `main()`. Wires argument parsing to `ClaudeUsage` (or `SessionReport` for `report`) and renders the result.
- `claude_usage.py` — `ClaudeUsage` class. Owns log parsing, aggregation, and rendering for the period reports.
- `session_report.py` — `SessionReport`. The `report` subcommand: per-session breakdown (project, active time, prompts, tools, edited files, lines, cost). Reuses `ClaudeUsage`'s dedup key, pricing cache, and energy/water constants.
- `pricing_cache.py` — `PricingCache`. Owns fetching/caching/looking up model prices.
- `utils.py` — `build_parser()` (argparse subcommands `daily`/`weekly`/`monthly`/`report`, all sharing the same flags; `--markdown` only affects `report`) and `parse_date()` for `YYYYMMDD`.

### Session report

`SessionReport.collect()` makes its own pass over the transcripts because it needs every `user`/`assistant` entry, not just ones with `usage`. Things that are easy to get wrong:

- Entries are attributed by their `sessionId`, not by file.
- One assistant message is split across several lines (one per content block), each repeating `usage`. Usage is deduped with `ClaudeUsage._record_key()`, tool calls by `tool_use.id`, and tool results by `tool_use_id`.
- Edits are counted from successful `tool_result`s (not `is_error`) whose call was an `EDIT_TOOLS` tool. Lines come from `toolUseResult.structuredPatch`, or `content` line count for a `Write` with `type: "create"`. Calls and results are matched after all files are read, since they can land in different files.
- Prompts are `user` entries with text content that aren't `isMeta`, `isCompactSummary`, sidechain (subagent), or `<local-command-stdout>`.
- Active minutes sum gaps between entries, skipping gaps over `IDLE_GAP` (15 min).
- `--since`/`--until` filter whole sessions by start date, unlike the period reports, which filter per message.

### Data flow

`iter_usage_records()` globs `**/*.jsonl` under the projects dir and yields one record per assistant message that carries a `usage` block. It skips non-`assistant` entries, malformed JSON lines, and unreadable files rather than failing.

**Deduplication matters**: Claude Code writes the same message into more than one transcript file. `_record_key()` keys on `message.id` + `requestId`, falling back to a SHA-256 of the raw line. Preserve this when touching the parser or costs will be inflated.

`aggregate()` buckets records by `period_key()` (`%Y-%m-%d`, ISO `YYYY-Www`, or `%Y-%m`) and multiplies four token counts by four per-token rates. The four categories — `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` — are threaded consistently through record dicts, group dicts, the table, and the JSON output; adding a fifth means touching all of them.

`aggregate()` also estimates `energy_wh` and `water_l` per group from `input_tokens + output_tokens` using the `WH_PER_TOKEN`/`L_PER_TOKEN` constants on `ClaudeUsage`. Those rates are calibrated off a single external data point (a published "median Claude Code session": ~500k input + ~90k output tokens ≈ 41 Wh / 0.082 L) and are estimates, not measurements — cache tokens are intentionally excluded since the source data doesn't cover them. Threaded through the same places as the cost field: group dict, `print_table()`, `groups_to_json()`.

### Pricing

Prices come from LiteLLM's `model_prices_and_context_window.json` on GitHub, cached to `.cache/model_prices.json` at the repo root (gitignored) with a 24-hour TTL.

The cache is designed to degrade rather than fail: a failed fetch falls back to the stale cache, and if there is no cache at all, `load_pricing()` returns `{}` and every cost renders as `$0.00` with a warning on stderr. Keep that offline path intact.

`price_for_model()` resolves names loosely because transcript model IDs don't always match LiteLLM keys: exact match → `anthropic/`-prefixed → prefix-stripped → suffix match → zero-cost dict. Unknown models (e.g. the `<synthetic>` pseudo-model that appears in transcripts) fall through to zero cost and still show up in the `Models` column.
