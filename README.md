# claude-usage

Claude Code token usage and cost reports for \*nix systems.

Reads the JSONL transcript logs that Claude Code writes to `~/.claude/projects/`, aggregates token usage by day, week, or month, and prices it using [LiteLLM's model price table](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json).

Pure Python 3 standard library — no dependencies, no install step.

## Usage

```bash
python3 claude_usage daily
python3 claude_usage weekly
python3 claude_usage monthly
```

```bash
Date | Input | Output | Cache Create | Cache Read | Cost (USD) | Energy (Wh) | Water (L) | Models
-----------+-------+--------+--------------+------------+------------+-------------+-----------+------------------------------
2026-07-22 | 4,590 | 12,790 | 34,849 | 329,789 | $0.73 | 1.2 | 0.002 | <synthetic>, claude-opus-4-8
2026-07-23 | 4 | 671 | 9,403 | 33,602 | $0.09 | 0.0 | 0.000 | claude-opus-4-8
-----------+-------+--------+--------------+------------+------------+-------------+-----------+------------------------------
TOTAL | 4,594 | 13,461 | 44,252 | 363,391 | $0.82 | 1.3 | 0.003 |
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
python3 claude_usage daily --since 20260701 --until 20260731

# Machine-readable monthly totals
python3 claude_usage monthly --json
```

```json
{
  "2026-06": {
    "input_tokens": 75864,
    "output_tokens": 465762,
    "cache_creation_tokens": 1986898,
    "cache_read_tokens": 19137683,
    "cost_usd": 32.0111,
    "energy_wh": 37.64,
    "water_l": 0.0753,
    "models": [
      "claude-fable-5",
      "claude-haiku-4-5-20251001",
      "claude-opus-4-8",
      "claude-sonnet-4-6"
    ]
  }
}
```

## Export as a zipapp (for easier usage on other machines)

```bash
python3 -m zipapp claude_usage -p "/usr/bin/env python3" -o bin/claude_usage
chmod +x bin/claude_usage

# Usage on another machine:
bin/claude_usage daily --since 20260601 --until 20260630
```

## Notes

- **Costs are estimates.** They are computed from published per-token rates, so the numbers reflect what the usage would cost on the API. If you're on a Claude subscription plan, this is not what you are billed.
- Claude Code sometimes records the same message in more than one transcript file. Messages are deduplicated by message and request ID so totals aren't inflated.
- The pricing table is cached in `.cache/model_prices.json` and refreshed at most once every 24 hours. If the download fails, the stale cache is used; with no cache at all, costs display as `$0.00` and a warning is printed to stderr.
- Models missing from the pricing table — such as the `<synthetic>` pseudo-model Claude Code writes for locally generated messages — are counted as free but still listed in the `Models` column.
- **Energy (Wh) and Water (L) are rough estimates**, not measurements. They're derived from a published per-token rate for a "median Claude Code session" (~500k input + ~90k output tokens ≈ 41 Wh / 0.082 L) applied uniformly to each period's input + output tokens. Cache tokens aren't factored in, since the source data doesn't break those out.

## License

MIT — see [LICENSE](LICENSE).
