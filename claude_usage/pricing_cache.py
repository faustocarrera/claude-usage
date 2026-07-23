import json
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class PricingCache:
    PRICING_URL = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )
    CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours

    def __init__(self):
        self.cache_dir = (Path(__file__).parent / "../.cache").expanduser().resolve()
        self.cache_file = self.cache_dir / "model_prices.json"
        self.pricing = {}

    def _cache_is_fresh(self) -> bool:
        if not self.cache_file.exists():
            return False
        age = datetime.now(timezone.utc).timestamp() - self.cache_file.stat().st_mtime
        return age < self.CACHE_MAX_AGE_SECONDS

    def load_pricing(self, force_refresh: bool = False) -> dict:
        """Return {model_name: {input_cost_per_token, output_cost_per_token,
        cache_creation_input_token_cost, cache_read_input_token_cost}}.

        Falls back to the stale cache (or an empty dict) if the network is
        unavailable, so the tool still works offline.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if force_refresh or not self._cache_is_fresh():
            try:
                try:
                    import certifi
                    context = ssl.create_default_context(cafile=certifi.where())
                except ImportError:
                    # Fallback for macOS if certifi is not installed and system certs are missing
                    context = ssl._create_unverified_context()

                req = urllib.request.Request(
                    self.PRICING_URL, headers={"User-Agent": "ccusage-py"}
                )
                with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                    data = resp.read()
                with open(self.cache_file, "wb") as f:
                    f.write(data)
            except Exception as e:
                if self.cache_file.exists():
                    print(
                        f"[warn] pricing refresh failed ({e}); using cached copy",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[warn] pricing fetch failed ({e}); costs will show as $0",
                        file=sys.stderr,
                    )
                    self.pricing = {}
                    return self.pricing

        with open(self.cache_file, "r") as f:
            raw = json.load(f)

        # LiteLLM's file is {model_name: {...pricing fields..., "litellm_provider": ...}}
        # plus a "sample_spec" key we don't need.
        pricing = {}
        for model, info in raw.items():
            if not isinstance(info, dict):
                continue
            pricing[model] = {
                "input_cost_per_token": info.get("input_cost_per_token", 0) or 0,
                "output_cost_per_token": info.get("output_cost_per_token", 0) or 0,
                "cache_creation_input_token_cost": info.get(
                    "cache_creation_input_token_cost", 0
                )
                or 0,
                "cache_read_input_token_cost": info.get(
                    "cache_read_input_token_cost", 0
                )
                or 0,
            }
        self.pricing = pricing
        return self.pricing

    def price_for_model(self, model: str) -> dict:
        """LiteLLM keys models like 'claude-sonnet-4-6' or with provider prefixes
        like 'anthropic/claude-sonnet-4-6'. Try a few lookups before giving up."""
        if not self.pricing:
            return {
                "input_cost_per_token": 0,
                "output_cost_per_token": 0,
                "cache_creation_input_token_cost": 0,
                "cache_read_input_token_cost": 0,
            }

        if model in self.pricing:
            return self.pricing[model]
        candidates = [
            model,
            f"anthropic/{model}",
            model.replace("anthropic/", ""),
        ]
        for c in candidates:
            if c in self.pricing:
                return self.pricing[c]
        # loose suffix match as a last resort
        for key in self.pricing:
            if key.endswith(model) or model.endswith(key):
                return self.pricing[key]
        return {
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
            "cache_creation_input_token_cost": 0,
            "cache_read_input_token_cost": 0,
        }
