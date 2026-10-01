"""API list prices for turning token counts into what they would cost on the API.

Prices come from LiteLLM's public price list, refreshed at most once a day
and kept in the cache directory; when that cannot be fetched or lacks a
model, the snapshot below is used. A model found in neither has no price,
and callers must treat it as unknown rather than free.

Known gaps, so the figure is an estimate, not a bill:
- Standard tier only. Long-context surcharges (OpenAI above 272K input
  tokens, older Claude models above 200K) are not applied; Codex compacts
  before that point, so it is rare in practice.
- Codex logs carry no cache write count, so OpenAI cache writes cost nothing.
"""

import json
import math
import os
import re
import tempfile
import time
import urllib.request

PRICES_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
FETCH_EVERY = 24 * 3600
RETRY_AFTER = 3600
MAX_BYTES = 32 * 1024 * 1024

# USD per million tokens: input, output, cache read, cache write (5 min),
# cache write (1 hour). Snapshot of the providers' pricing pages, 2026-10-01.
SNAPSHOT_DATE = "2026-10-01"
SNAPSHOT = {
    "claude-fable-5-1": (10, 50, 0.25, 12.5, 20),
    "claude-fable-5": (10, 50, 1, 12.5, 20),
    "claude-opus-5-5": (4, 20, 0.2, 5, 8),
    "claude-opus-5": (5, 25, 0.5, 6.25, 10),
    "claude-opus-4-8": (5, 25, 0.5, 6.25, 10),
    "claude-opus-4-7": (5, 25, 0.5, 6.25, 10),
    "claude-opus-4-6": (5, 25, 0.5, 6.25, 10),
    "claude-opus-4-5": (5, 25, 0.5, 6.25, 10),
    "claude-opus-4-1": (15, 75, 1.5, 18.75, 30),
    "claude-opus-4": (15, 75, 1.5, 18.75, 30),
    "claude-sonnet-5-5": (2, 10, 0.2, 2.5, 4),
    "claude-sonnet-5": (2, 10, 0.2, 2.5, 4),
    "claude-sonnet-4-6": (3, 15, 0.3, 3.75, 6),
    "claude-sonnet-4-5": (3, 15, 0.3, 3.75, 6),
    "claude-sonnet-4": (3, 15, 0.3, 3.75, 6),
    "claude-haiku-4-5": (1, 5, 0.1, 1.25, 2),
    "gpt-6-astra": (10, 50, 1, 12.5, 12.5),
    "gpt-6-sol": (2, 10, 0.2, 2.5, 2.5),
    "gpt-6-luna": (0.1, 0.5, 0.01, 0.125, 0.125),
    "gpt-5.6-sol": (4, 20, 0.4, 5, 5),
    "gpt-5.6-terra": (2, 12, 0.2, 2.5, 2.5),
    "gpt-5.6-luna": (0.2, 1.2, 0.02, 0.25, 0.25),
    "gpt-5.5": (5, 30, 0.5, 0, 0),
    "gpt-5.4": (2.5, 15, 0.25, 0, 0),
    "gpt-5.4-mini": (0.75, 4.5, 0.075, 0, 0),
    "gpt-5.3-codex": (1.75, 14, 0.175, 0, 0),
    "gpt-5.2": (1.75, 14, 0.175, 0, 0),
    "gpt-5.1": (1.25, 10, 0.125, 0, 0),
    "gpt-5.1-codex": (1.25, 10, 0.125, 0, 0),
    "gpt-5.1-codex-max": (1.25, 10, 0.125, 0, 0),
    "gpt-5.1-codex-mini": (0.25, 2, 0.025, 0, 0),
    "gpt-5": (1.25, 10, 0.125, 0, 0),
    "gpt-5-codex": (1.25, 10, 0.125, 0, 0),
    "gpt-5-mini": (0.25, 2, 0.025, 0, 0),
    "gpt-5-nano": (0.05, 0.4, 0.005, 0, 0),
}

DATE_SUFFIX = re.compile(r"-\d{8}$")


def _rate(v):
    """Per-token USD from the price list as USD per million, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v) * 1e6
    return v if math.isfinite(v) and 0 <= v < 1e4 else None


def _from_litellm(raw):
    """Keep only plain model ids from Anthropic and OpenAI with usable rates."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, v in raw.items():
        if not isinstance(name, str) or not isinstance(v, dict) or "/" in name:
            continue
        if v.get("litellm_provider") not in ("anthropic", "openai"):
            continue
        inp, outp = _rate(v.get("input_cost_per_token")), _rate(v.get("output_cost_per_token"))
        if inp is None or outp is None:
            continue
        read = _rate(v.get("cache_read_input_token_cost"))
        write = _rate(v.get("cache_creation_input_token_cost"))
        write1h = _rate(v.get("cache_creation_input_token_cost_above_1hr"))
        if read is None:
            read = inp
        if write is None:
            write = 0.0
        out[name] = [inp, outp, read, write, write1h if write1h is not None else write]
    return out


def _fetch():
    req = urllib.request.Request(PRICES_URL, headers={"User-Agent": "agents-usage"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("price list too large")
    return _from_litellm(json.loads(data))


def _write(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def load(cache_dir):
    """Return (lookup function, date the prices are from)."""
    path = os.path.join(cache_dir, "prices.json")
    try:
        with open(path) as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            stored = {}
    except (OSError, ValueError):
        stored = {}

    now = time.time()

    def stamp(v):
        # A timestamp in the future (clock was wrong) would block refreshes.
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= now
        return v if ok else 0

    fetched_at = stamp(stored.get("fetchedAt"))
    tried_at = stamp(stored.get("triedAt"))
    models = stored.get("models") if isinstance(stored.get("models"), dict) else {}
    if now - fetched_at > FETCH_EVERY and now - tried_at > RETRY_AFTER:
        try:
            fresh = _fetch()
            if fresh:
                models, fetched_at = fresh, now
        except Exception:
            # Offline or a malformed list: keep what is stored and retry later.
            pass
        try:
            _write(path, {"fetchedAt": fetched_at, "triedAt": now, "models": models})
        except OSError:
            pass

    live = {}
    for name, rates in models.items():
        if isinstance(rates, list) and len(rates) == 5:
            vals = [_rate(r / 1e6) if isinstance(r, (int, float)) and not isinstance(r, bool) else None for r in rates]
            if None not in vals:
                live[name] = tuple(vals)

    def lookup(model):
        for key in (model, DATE_SUFFIX.sub("", model)):
            if key in live:
                return live[key]
            if key in SNAPSHOT:
                return SNAPSHOT[key]
        return None

    as_of = time.strftime("%Y-%m-%d", time.localtime(fetched_at)) if live else SNAPSHOT_DATE
    return lookup, as_of


def cost(rates, comps):
    """USD for [input, output, cache read, cache write 5m, cache write 1h]."""
    return sum(r * c for r, c in zip(rates, comps)) / 1e6
