# claude-usage

Claude Code token usage and cost reports for \*nix systems.

Reads the JSONL transcript logs that Claude Code writes to `~/.claude/projects/`, aggregates token usage by day, week, or month, and prices it using [LiteLLM's model price table](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json).

Pure Python 3 standard library — no dependencies, no install step.

## Usage

```bash
python3 claude-usage daily
python3 claude-usage weekly
python3 claude-usage monthly
```

```bash
Date | Input | Output | Cache Create | Cache Read | Cost (USD) | Models
-----------+-------+--------+--------------+------------+------------+------------------------------
2026-07-22 | 4,590 | 12,790 | 34,849 | 329,789 | $0.73 | <synthetic>, claude-opus-4-8
2026-07-23 | 4 | 671 | 9,403 | 33,602 | $0.09 | claude-opus-4-8
-----------+-------+--------+--------------+------------+------------+------------------------------
TOTAL | 4,594 | 13,461 | 44,252 | 363,391 | $0.82 |
```

### Options

All three subcommands accept the same flags:

| Flag                | Description                                                    |
| ------------------- | -------------------------------------------------------------- |
| `--since YYYYMMDD`  | Only include usage on or after this date                       |
| `--until YYYYMMDD`  | Only include usage on or before this date                      |
| `--json`            | Emit JSON instead of a table                                   |
| `--path PATH`       | Override the projects directory (default `~/.claude/projects`) |
| `--refresh-pricing` | Force a re-download of the pricing table                       |

### Examples

```bash
# Cost for a single month
python3 claude-usage daily --since 20260701 --until 20260731

# Machine-readable monthly totals
python3 claude-usage monthly --json
```

```json
{
  "2026-06": {
    "input_tokens": 75864,
    "output_tokens": 465762,
    "cache_creation_tokens": 1986898,
    "cache_read_tokens": 19137683,
    "cost_usd": 32.0111,
    "models": [
      "claude-fable-5",
      "claude-haiku-4-5-20251001",
      "claude-opus-4-8",
      "claude-sonnet-4-6"
    ]
  }
}
```

## Notes

- **Costs are estimates.** They are computed from published per-token rates, so the numbers reflect what the usage would cost on the API. If you're on a Claude subscription plan, this is not what you are billed.
- Claude Code sometimes records the same message in more than one transcript file. Messages are deduplicated by message and request ID so totals aren't inflated.
- The pricing table is cached in `.cache/model_prices.json` and refreshed at most once every 24 hours. If the download fails, the stale cache is used; with no cache at all, costs display as `$0.00` and a warning is printed to stderr.
- Models missing from the pricing table — such as the `<synthetic>` pseudo-model Claude Code writes for locally generated messages — are counted as free but still listed in the `Models` column.

## License

MIT — see [LICENSE](LICENSE).
