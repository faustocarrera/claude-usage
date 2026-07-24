# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`claude-usage` is a zero-dependency Python CLI (stdlib only) that reports Claude Code token usage and cost by reading the local JSONL transcript logs in `~/.claude/projects/`.

## Running

```bash
python3 claude-usage daily                          # also: weekly, monthly
python3 claude-usage daily --since 20260701 --until 20260723
python3 claude-usage monthly --json
python3 claude-usage daily --path /some/other/projects
python3 claude-usage daily --refresh-pricing        # force pricing re-download
```

There is no build step, no packaging (`setup.py`/`pyproject.toml`), no test suite, and no lint config. Verify changes by running the CLI directly.

## Import layout — read before adding modules

The package directory is `claude-usage/` (hyphenated), so it is **not importable as a Python package**. It only works because `python3 claude-usage` executes `claude-usage/__main__.py` with that directory prepended to `sys.path`.

Consequences:
- Modules use **flat imports** (`from pricing_cache import PricingCache`), never relative (`from .pricing_cache import ...`). Relative imports will break the entry point.
- `claude-usage/__init__.py` is vestigial — it is never executed under this invocation. Don't put logic there.

## Architecture

Four modules, one responsibility each:

- `__main__.py` — `main()`. Wires argument parsing to `ClaudeUsage` and renders the result.
- `claude_usage.py` — `ClaudeUsage` class. Owns log parsing, aggregation, and rendering.
- `pricing_cache.py` — `PricingCache`. Owns fetching/caching/looking up model prices.
- `utils.py` — `build_parser()` (argparse subcommands `daily`/`weekly`/`monthly`, all sharing the same flags) and `parse_date()` for `YYYYMMDD`.

### Data flow

`iter_usage_records()` globs `**/*.jsonl` under the projects dir and yields one record per assistant message that carries a `usage` block. It skips non-`assistant` entries, malformed JSON lines, and unreadable files rather than failing.

**Deduplication matters**: Claude Code writes the same message into more than one transcript file. `_record_key()` keys on `message.id` + `requestId`, falling back to a SHA-256 of the raw line. Preserve this when touching the parser or costs will be inflated.

`aggregate()` buckets records by `period_key()` (`%Y-%m-%d`, ISO `YYYY-Www`, or `%Y-%m`) and multiplies four token counts by four per-token rates. The four categories — `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` — are threaded consistently through record dicts, group dicts, the table, and the JSON output; adding a fifth means touching all of them.

### Pricing

Prices come from LiteLLM's `model_prices_and_context_window.json` on GitHub, cached to `.cache/model_prices.json` at the repo root (gitignored) with a 24-hour TTL.

The cache is designed to degrade rather than fail: a failed fetch falls back to the stale cache, and if there is no cache at all, `load_pricing()` returns `{}` and every cost renders as `$0.00` with a warning on stderr. Keep that offline path intact.

`price_for_model()` resolves names loosely because transcript model IDs don't always match LiteLLM keys: exact match → `anthropic/`-prefixed → prefix-stripped → suffix match → zero-cost dict. Unknown models (e.g. the `<synthetic>` pseudo-model that appears in transcripts) fall through to zero cost and still show up in the `Models` column.
