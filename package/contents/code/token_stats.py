#!/usr/bin/env python3
"""Print daily and per-model token totals from local Claude Code and Codex logs.

Claude Code writes one JSON line per API response under ~/.claude/projects,
the Codex CLI writes cumulative token counters into ~/.codex/sessions. Both are
append-only, so each file is parsed once and afterwards only from the byte
offset reached last time; the per-file results live in a cache. Only the last
RETENTION_DAYS days are kept.

Output: {generatedAt, scanning, daily: [{date, claude, codex}] (last 30 days),
periods: {"1"|"7"|"30": {claude, codex, models: [Model]}}} where a Model is
{name, provider, total, input, output, cacheRead, cacheWrite}.
"""

import fcntl
import json
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CODEX_DIRS = [os.path.join(CODEX_HOME, "sessions"), os.path.join(CODEX_HOME, "archived_sessions")]
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "agents-usage")
CACHE_FILE = os.path.join(CACHE_DIR, "tokens-v2.json")
RETENTION_DAYS = 90
WORKERS = 4



def local_day(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()


def add(contrib, day, model, comps):
    slot = contrib.setdefault(day, {}).setdefault(model, [0, 0, 0, 0])
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
            comps = [
                usage.get("input_tokens") or 0,
                usage.get("output_tokens") or 0,
                usage.get("cache_read_input_tokens") or 0,
                usage.get("cache_creation_input_tokens") or 0,
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


def summarise(cache, today):
    days30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    daily = {d: {"date": d, "claude": 0, "codex": 0} for d in days30}
    periods = {}
    for n in (1, 7, 30):
        periods[str(n)] = {"first": (today - timedelta(days=n - 1)).isoformat(), "claude": 0, "codex": 0, "models": {}}

    for entry in cache["files"].values():
        provider = entry["kind"]
        for day, models in entry["contrib"].items():
            for model, comps in models.items():
                total = sum(comps)
                if day in daily:
                    daily[day][provider] += total
                for p in periods.values():
                    if day < p["first"]:
                        continue
                    p[provider] += total
                    name = pretty_model(provider, model)
                    slot = p["models"].setdefault((provider, name), [0, 0, 0, 0])
                    for i, v in enumerate(comps):
                        slot[i] += v

    out = {}
    for key, p in periods.items():
        models = [
            {"name": name, "provider": provider, "total": sum(c), "input": c[0], "output": c[1], "cacheRead": c[2], "cacheWrite": c[3]}
            for (provider, name), c in p["models"].items()
        ]
        models.sort(key=lambda m: m["total"], reverse=True)
        out[key] = {"claude": p["claude"], "codex": p["codex"], "models": models}
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scanning": False,
        "daily": [daily[d] for d in days30],
        "periods": out,
    }


def main():
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    today = date.today()
    # One scan at a time; a second widget instance waits and reuses the result.
    with open(os.path.join(CACHE_DIR, "tokens.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = load_cache()
        update(cache, today)
        write_cache(cache)
    print(json.dumps(summarise(cache, today)))


if __name__ == "__main__":
    main()
