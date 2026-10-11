#!/usr/bin/env python3
"""Print daily and per-model token totals from local Claude Code and Codex
logs, plus account-wide Z.ai usage.

Claude Code writes one JSON line per API response under ~/.claude/projects,
the Codex CLI writes cumulative token counters into ~/.codex/sessions. Both are
append-only, so each file is parsed once and afterwards only from the byte
offset reached last time; the per-file results live in a cache. Only the last
RETENTION_DAYS days are kept. The Z.ai series comes from the console's
account-wide monitor API and covers every tool that uses the plan's key.

Output: {generatedAt, scanning, daily: [{date, claude, codex, zai}] (last 365 days),
pricesAsOf, periods: {"1"|"7"|"30"|"60"|"90"|"365": {claude, codex, zai,
cost: {claude, codex, zai}, unpriced: {claude, codex, zai},
models: [Model]}}} where a Model is
{name, provider, total, input, output, cacheRead, cacheWrite, cost}. cost is the
USD the tokens would cost at API list prices; tokens of models without a known
price are left out of it and counted in unpriced, and such a Model has cost null.
"""

import fcntl
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone

# The widget runs this with python3 -I, which leaves the script's own
# directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prices  # noqa: E402

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CODEX_DIRS = [os.path.join(CODEX_HOME, "sessions"), os.path.join(CODEX_HOME, "archived_sessions")]
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "agents-usage")
CACHE_FILE = os.path.join(CACHE_DIR, "tokens-v6.json")
RETENTION_DAYS = 366
WORKERS = 4

# Account-wide Z.ai usage covers every tool that uses the plan's key, not
# only the ones whose local logs are scanned above. The endpoint is the
# console's undocumented monitor API, so parse defensively.
ZAI_MODEL_USAGE_URL = "https://api.z.ai/api/monitor/usage/model-usage"
ZAI_TTL = 600
UTC8 = timezone(timedelta(hours=8))


def local_day(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()


def add(contrib, day, model, comps):
    slot = contrib.setdefault(day, {}).setdefault(model, [0] * len(comps))
    for i, v in enumerate(comps):
        slot[i] += v


def read_new_lines(path, offset, wanted):
    """Return (new complete lines for which wanted(line) is true, new offset).

    Streams line by line so a multi-hundred-MB transcript is never held in
    memory; a trailing partial line is left for the next run.
    """
    lines = []
    with open(path, "rb") as f:
        f.seek(offset)
        for line in f:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            if wanted(line):
                lines.append(line)
    return lines, offset


def parse_claude(path, offset):
    """Return ({message id: (day, model, comps)}, new offset)."""
    # Cheap substring test first: most lines are tool output, not responses.
    lines, offset = read_new_lines(path, offset, lambda l: b'"usage"' in l and b'"assistant"' in l)
    records = {}
    for line in lines:
        try:
            d = json.loads(line)
            msg = d["message"]
            usage = msg["usage"]
            model = msg.get("model") or "?"
            if model == "<synthetic>":
                continue
            # A response with several content blocks is written once per block
            # with the same usage, so the message id (+ request id) is the key.
            key = "%s:%s" % (msg.get("id"), d.get("requestId"))
            # Cache writes are priced by TTL. Without the split, assume the
            # API default of 5 minutes.
            write = usage.get("cache_creation_input_tokens") or 0
            write1h = min(write, (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
            comps = [
                usage.get("input_tokens") or 0,
                usage.get("output_tokens") or 0,
                usage.get("cache_read_input_tokens") or 0,
                write - write1h,
                write1h,
            ]
            # Early lines of a response carry partial output counts; keep the max.
            if key in records:
                comps = [max(a, b) for a, b in zip(records[key][2], comps)]
                records[key] = (records[key][0], records[key][1], comps)
            else:
                records[key] = (local_day(d["timestamp"]), model, comps)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return records, offset


def parse_codex(path, offset, state):
    """Return (contrib, new offset, new state) from Codex token_count events.

    The counters are cumulative per session, so usage is the difference from
    the previous event; repeated events with an unchanged total add nothing.
    """
    lines, offset = read_new_lines(path, offset, lambda l: b'"turn_context"' in l or b'"token_count"' in l)
    model = state.get("model") or "codex"
    prev = state.get("prev")
    contrib = {}
    for line in lines:
        if b'"turn_context"' in line:
            try:
                model = json.loads(line)["payload"].get("model") or model
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
            continue
        try:
            d = json.loads(line)
            info = d["payload"].get("info") or {}
            total = info.get("total_token_usage")
            if not total:
                continue
            cur = codex_comps(total)
            # A forked or resumed session starts with the parent's running
            # total, so the first event of a file only counts its own turn.
            delta = [c - p for c, p in zip(cur, prev)] if prev else [-1]
            if any(v < 0 for v in delta):
                # Counter went backwards (new context): count the last turn only.
                delta = codex_comps(info.get("last_token_usage") or {})
            prev = cur
            if sum(delta) > 0:
                add(contrib, local_day(d["timestamp"]), model, delta)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return contrib, offset, {"model": model, "prev": prev}


def codex_comps(u):
    cached = u.get("cached_input_tokens") or 0
    return [
        max(0, (u.get("input_tokens") or 0) - cached),
        u.get("output_tokens") or 0,
        cached,
        u.get("cache_write_input_tokens") or 0,
        0,
    ]


def parse(job):
    kind, path, offset, state = job
    try:
        if kind == "claude":
            records, new_offset = parse_claude(path, offset)
            return path, records, new_offset, None
        contrib, new_offset, new_state = parse_codex(path, offset, state)
        return path, contrib, new_offset, new_state
    except OSError:
        return path, None, offset, state


def list_files(cutoff_ts):
    found = {}
    for kind, roots in (("claude", [CLAUDE_DIR]), ("codex", CODEX_DIRS)):
        for root in roots:
            for dirpath, _, names in os.walk(root):
                for n in names:
                    if not n.endswith(".jsonl"):
                        continue
                    p = os.path.join(dirpath, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_mtime >= cutoff_ts:
                        found[p] = (kind, st.st_size, st.st_mtime)
    return found


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        if isinstance(cache.get("files"), dict):
            return cache
    except (OSError, ValueError, AttributeError):
        pass
    return {"files": {}}


def write_cache(cache):
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f, separators=(",", ":"))
        os.replace(tmp, CACHE_FILE)
    except BaseException:
        os.unlink(tmp)
        raise


def update(cache, today):
    cutoff = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    cutoff_ts = datetime.combine(today - timedelta(days=RETENTION_DAYS), datetime.min.time()).timestamp()
    files = cache["files"]
    found = list_files(cutoff_ts)
    for p in list(files):
        if p not in found:
            del files[p]

    jobs = []
    for p, (kind, size, mtime) in found.items():
        entry = files.get(p)
        if entry and entry["size"] == size and entry["mtime"] == mtime:
            continue
        if not entry or size < entry["offset"]:
            # New or rewritten file: start over.
            entry = files[p] = {"kind": kind, "offset": 0, "state": {}, "contrib": {}, "ids": {}, "size": -1, "mtime": 0}
        jobs.append((kind, p, entry["offset"], entry["state"]))

    if jobs:
        with ProcessPoolExecutor(min(WORKERS, len(jobs))) as pool:
            results = list(pool.map(parse, jobs, chunksize=8))
    else:
        results = []

    # Claude Code copies earlier messages into resumed and forked sessions,
    # so ids are deduplicated across all files, not only within one.
    # ids map to [day, model, comps] so a later, larger count for the same
    # response adds only the difference.
    owner = {}
    for p, entry in files.items():
        if entry["kind"] == "claude":
            for short in entry["ids"]:
                owner.setdefault(short, p)

    for path, data, offset, state in results:
        entry = files[path]
        if data is None:
            # Read failed: leave size/mtime stale so the file is retried.
            continue
        entry["offset"] = offset
        entry["size"], entry["mtime"] = found[path][1], found[path][2]
        if entry["kind"] == "claude":
            for key, (day, model, comps) in data.items():
                short = key[-24:]
                if owner.setdefault(short, path) != path:
                    continue
                old = entry["ids"].get(short)
                if old is None:
                    entry["ids"][short] = [day, model, comps]
                    add(entry["contrib"], day, model, comps)
                    continue
                new = [max(a, b) for a, b in zip(old[2], comps)]
                diff = [a - b for a, b in zip(new, old[2])]
                if any(diff):
                    old[2] = new
                    add(entry["contrib"], old[0], old[1], diff)
        else:
            entry["state"] = state
            for day, models in data.items():
                for model, comps in models.items():
                    add(entry["contrib"], day, model, comps)

    for entry in files.values():
        for day in [d for d in entry["contrib"] if d < cutoff]:
            del entry["contrib"][day]


# Z.ai account usage


def zai_day(bucket):
    """A bucket label ("2026-10-04 13:00" or "2026-10-04") is a UTC+8 time."""
    s = (bucket or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC8).astimezone().date().isoformat()
        except ValueError:
            continue
    return None


def _zai_window(key, start, end):
    """One model-usage request; returns the data body or raises."""
    url = ZAI_MODEL_USAGE_URL + "?" + urllib.parse.urlencode({
        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
    })
    req = urllib.request.Request(url, headers={
        "Authorization": key,
        "User-Agent": "agents-usage-plasmoid",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    # Failures ride in a 200 envelope (M5-style): check it, not the status.
    if not isinstance(data, dict) or data.get("code") != 200 or data.get("success") is not True:
        raise ValueError("zai usage envelope: %s" % (data.get("msg"),))
    return data.get("data") or {}


def fetch_zai_usage():
    """Return ({day: tokens}, {model: {day: tokens}}) for the whole account.

    The server rejects ranges past about a month, so the retention window is
    walked in 30-day chunks whose bucket labels cannot overlap.
    """
    key = None
    try:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
        with open(os.path.join(base, "opencode", "auth.json")) as f:
            key = (json.load(f).get("zai-coding-plan") or {}).get("key")
    except (OSError, ValueError, AttributeError):
        pass
    if not key:
        return None
    end = datetime.now(UTC8)
    start = end - timedelta(days=RETENTION_DAYS)
    days, model_days = {}, {}
    chunk_start, step = start, timedelta(days=30)
    while chunk_start <= end:
        chunk_end = min(chunk_start + step - timedelta(seconds=1), end)
        body = _zai_window(key, chunk_start, chunk_end)
        x = body.get("x_time") or []
        for i, total in enumerate(body.get("tokensUsage") or []):
            if i >= len(x):
                break
            day = zai_day(x[i])
            if day and total:
                days[day] = days.get(day, 0) + total
        for m in body.get("modelDataList") or []:
            name = m.get("modelName")
            if not name:
                continue
            for i, total in enumerate(m.get("tokensUsage") or []):
                if i >= len(x):
                    break
                day = zai_day(x[i])
                if day and total:
                    slot = model_days.setdefault(name, {})
                    slot[day] = slot.get(day, 0) + total
        if chunk_end >= end:
            break
        chunk_start = chunk_end + timedelta(seconds=1)
    return days, model_days


def refresh_zai(cache, today):
    """Refresh the account-wide Z.ai usage at most every ZAI_TTL seconds.

    A failed fetch keeps the previous data; without any, the provider simply
    stays empty until a later refresh succeeds.
    """
    entry = cache.get("zai") or {}
    if time.time() - entry.get("fetchedAt", 0) < ZAI_TTL:
        return
    try:
        result = fetch_zai_usage()
    except (OSError, ValueError, urllib.error.URLError):
        return
    if not result:
        return
    cutoff = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    days, model_days = result
    days = {d: v for d, v in days.items() if d >= cutoff}
    model_days = {m: {d: v for d, v in s.items() if d >= cutoff} for m, s in model_days.items()}
    cache["zai"] = {"fetchedAt": time.time(), "days": days, "modelDays": model_days}


CLAUDE_NEW = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d{1,2})?)(?:-\d{8})?$")
CLAUDE_OLD = re.compile(r"^claude-(\d+(?:-\d{1,2})?)-([a-z]+)(?:-\d{8})?$")


def pretty_model(provider, model):
    if provider == "claude":
        m = CLAUDE_NEW.match(model)
        if m:
            return "%s %s" % (m.group(1).capitalize(), m.group(2).replace("-", "."))
        m = CLAUDE_OLD.match(model)
        if m:
            return "%s %s" % (m.group(2).capitalize(), m.group(1).replace("-", "."))
    return model


def summarise(cache, today, price_of, prices_as_of):
    all_days = [(today - timedelta(days=i)).isoformat() for i in range(364, -1, -1)]
    daily = {d: {"date": d, "claude": 0, "codex": 0, "zai": 0} for d in all_days}
    periods = {}
    for n in (1, 7, 30, 60, 90, 365):
        periods[str(n)] = {
            "first": (today - timedelta(days=n - 1)).isoformat(),
            "claude": 0,
            "codex": 0,
            "zai": 0,
            "cost": {"claude": 0.0, "codex": 0.0, "zai": 0.0},
            "unpriced": {"claude": 0, "codex": 0, "zai": 0},
            "models": {},
        }

    rates = {}
    for entry in cache["files"].values():
        provider = entry["kind"]
        for day, models in entry["contrib"].items():
            for model, comps in models.items():
                total = sum(comps)
                if day in daily:
                    daily[day][provider] += total
                if model not in rates:
                    rates[model] = price_of(model)
                cost = prices.cost(rates[model], comps) if rates[model] else None
                for p in periods.values():
                    if day < p["first"]:
                        continue
                    p[provider] += total
                    name = pretty_model(provider, model)
                    slot = p["models"].setdefault((provider, name), {"comps": [0] * 5, "cost": 0.0, "unpriced": 0})
                    for i, v in enumerate(comps):
                        slot["comps"][i] += v
                    if cost is None:
                        slot["unpriced"] += total
                        p["unpriced"][provider] += total
                    else:
                        slot["cost"] += cost
                        p["cost"][provider] += cost

    # Account-wide Z.ai usage: every tool sharing the plan's key, local logs
    # or not. Its day totals are the sum of the per-model series.
    zai_entry = cache.get("zai") or {}
    for model, days in (zai_entry.get("modelDays") or {}).items():
        if model not in rates:
            rates[model] = price_of(model)
        for day, total in days.items():
            if day in daily:
                daily[day]["zai"] += total
            if total <= 0:
                continue
            cost = prices.cost(rates[model], [total, 0, 0, 0, 0]) if rates[model] else None
            for p in periods.values():
                if day < p["first"]:
                    continue
                p["zai"] += total
                slot = p["models"].setdefault(("zai", model), {"comps": [0] * 5, "cost": 0.0, "unpriced": 0})
                slot["comps"][0] += total
                if cost is None:
                    slot["unpriced"] += total
                    p["unpriced"]["zai"] += total
                else:
                    slot["cost"] += cost
                    p["cost"]["zai"] += cost

    out = {}
    for key, p in periods.items():
        models = []
        for (provider, name), slot in p["models"].items():
            c = slot["comps"]
            total = sum(c)
            models.append({
                "name": name,
                "provider": provider,
                "total": total,
                "input": c[0],
                "output": c[1],
                "cacheRead": c[2],
                "cacheWrite": c[3] + c[4],
                "cost": None if slot["unpriced"] >= total else round(slot["cost"], 4),
            })
        models.sort(key=lambda m: m["total"], reverse=True)
        out[key] = {
            "claude": p["claude"],
            "codex": p["codex"],
            "zai": p["zai"],
            "cost": {k: round(v, 4) for k, v in p["cost"].items()},
            "unpriced": p["unpriced"],
            "models": models,
        }
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scanning": False,
        "pricesAsOf": prices_as_of,
        "daily": [daily[d] for d in all_days],
        "periods": out,
    }


def main():
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    # Earlier cache formats hold the same log metadata; do not leave them behind.
    for old in ("tokens-v1.json", "tokens-v2.json", "tokens-v3.json", "tokens-v4.json", "tokens-v5.json", "tokens-v7.json", "tokens-v8.json"):
        try:
            os.unlink(os.path.join(CACHE_DIR, old))
        except FileNotFoundError:
            pass
    today = date.today()
    # One scan at a time; a second widget instance waits and reuses the result.
    with open(os.path.join(CACHE_DIR, "tokens.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = load_cache()
        update(cache, today)
    # Outside the lock: the network fetch and a slow price load must not hold
    # up another instance.
    refresh_zai(cache, today)
    write_cache(cache)
    price_of, prices_as_of = prices.load(CACHE_DIR)
    print(json.dumps(summarise(cache, today, price_of, prices_as_of)))


if __name__ == "__main__":
    main()
