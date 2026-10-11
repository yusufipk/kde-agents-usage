#!/usr/bin/env python3
"""Print remaining usage limits for Claude, OpenAI Codex, Z.ai GLM and
OpenCode Go as JSON.

Reads the credentials these tools already store on disk — Claude Code and the
Codex CLI their OAuth tokens, OpenCode the API keys of the Z.ai GLM Coding
Plan and OpenCode Go — and asks each service for the current rate-limit
windows. Stdlib only, so the plasmoid needs nothing beyond python3.

Output: {"fetchedAt": ISO, "providers": [Provider, ...]} where a Provider is
{id, name, plan, ok, stale, errorCode, errorDetail,
windows: [{key, model, usedPercent, resetsAt}]}. Error codes and window keys
are turned into localised text by the QML side.
The last good result per provider is cached, so a failed fetch still shows the
previous numbers with stale=true.
"""

import base64
import fcntl
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CLAUDE_CREDS = os.path.join(HOME, ".claude", ".credentials.json")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CODEX_AUTH = os.path.join(CODEX_HOME, "auth.json")
OPENCODE_AUTH = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"),
    "opencode", "auth.json")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "agents-usage")
CACHE_FILE = os.path.join(CACHE_DIR, "limits-v2.json")

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
ZAI_USAGE_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
OPENCODE_GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
# Public OAuth client id of the Codex CLI (the same value the CLI sends).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TIMEOUT = 15


class FetchError(Exception):
    """A known failure, reported to the UI as an error code plus detail."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib forwards every header, Authorization included, to any redirect
    # target (even plain http), so bearer requests must never follow one.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


OPENER = urllib.request.build_opener(NoRedirect)


def http_json(url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    with OPENER.open(req, timeout=TIMEOUT) as resp:
        try:
            return json.load(resp)
        except ValueError:
            # A 200 body that is not JSON (a challenge page, an empty body)
            # is not a credentials problem, which is what run() would
            # otherwise report for the ValueError this raises.
            raise FetchError("bad_response")


def iso_from_epoch(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def window(key, used, resets_at, model=None):
    return {"key": key, "model": model, "usedPercent": max(0.0, min(100.0, float(used))), "resetsAt": resets_at}


# Claude


def fetch_claude():
    try:
        with open(CLAUDE_CREDS) as f:
            oauth = json.load(f)["claudeAiOauth"]
    except (OSError, ValueError, KeyError):
        raise FetchError("claude_no_login")

    # The token is only read, never refreshed: Claude Code rotates the refresh
    # token itself and a second writer could log it out.
    if oauth.get("expiresAt", 0) / 1000 < time.time():
        raise FetchError("claude_expired")

    try:
        data = http_json(CLAUDE_USAGE_URL, {
            "Authorization": "Bearer " + oauth["accessToken"],
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "agents-usage-plasmoid",
            "Accept": "application/json",
        })
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("claude_invalid")
        raise FetchError("http", str(e.code))

    windows = []
    for key in ("five_hour", "seven_day"):
        w = data.get(key)
        if w and w.get("utilization") is not None:
            windows.append(window("session" if key == "five_hour" else "weekly", w["utilization"], w.get("resets_at")))

    # Per-model weekly caps (e.g. a separate Fable or Opus budget) only appear
    # in the newer "limits" list.
    for lim in data.get("limits") or []:
        if lim.get("kind") != "weekly_scoped":
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if model and lim.get("percent") is not None:
            windows.append(window("weekly_scoped", lim["percent"], lim.get("resets_at"), model))

    plan = oauth.get("subscriptionType")
    return {"plan": plan.capitalize() if plan else None, "windows": windows}


# Codex


def jwt_claims(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def write_json_atomic(path, obj):
    # Resolve symlinks so a dotfiles-managed link is updated, not replaced.
    path = os.path.realpath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def refresh_codex(auth):
    """Refresh the Codex tokens the way the CLI does and write them back.

    The refresh token rotates, so the new one must be persisted or the CLI
    itself would be logged out on its next run. A Codex CLI session that is
    running at that moment still holds the old refresh token in memory, which
    is why this only happens once the access token has actually expired.
    """
    try:
        new = http_json(CODEX_TOKEN_URL, {"User-Agent": "agents-usage-plasmoid"}, {
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": auth["tokens"]["refresh_token"],
            "scope": "openid profile email",
        })
    except urllib.error.HTTPError:
        raise FetchError("codex_refresh_failed")
    sent = auth["tokens"]["refresh_token"]
    # Re-read right before writing: if the CLI or a new `codex login` changed
    # the file during the request, keep its tokens instead of overwriting them.
    current = load_codex_auth()
    if current["tokens"].get("refresh_token") != sent:
        return current
    for k in ("id_token", "access_token", "refresh_token"):
        if new.get(k):
            current["tokens"][k] = new[k]
    current["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        write_json_atomic(CODEX_AUTH, current)
    except OSError as e:
        raise FetchError("codex_save_failed", e.strerror)
    return current


CODEX_PLAN_NAMES = {"prolite": "Pro Lite", "pro": "Pro", "plus": "Plus", "team": "Team", "business": "Business", "enterprise": "Enterprise", "edu": "Edu", "free": "Free"}


def load_codex_auth():
    try:
        with open(CODEX_AUTH) as f:
            auth = json.load(f)
        auth["tokens"]["access_token"]
        return auth
    except (OSError, ValueError, KeyError, TypeError):
        raise FetchError("codex_no_login")


class codex_lock:
    """Serialises refreshes between widget instances; the CLI does not take it."""

    def __enter__(self):
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        self.f = open(os.path.join(CACHE_DIR, "codex-refresh.lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def __exit__(self, *exc):
        self.f.close()


def fetch_codex():
    with codex_lock():
        auth = load_codex_auth()
        if jwt_claims(auth["tokens"]["access_token"]).get("exp", 0) < time.time() + 30:
            auth = refresh_codex(auth)
    tokens = auth["tokens"]

    def call(tok):
        headers = {
            "Authorization": "Bearer " + tok["access_token"],
            "User-Agent": "agents-usage-plasmoid",
            "Accept": "application/json",
        }
        if tok.get("account_id"):
            headers["ChatGPT-Account-Id"] = tok["account_id"]
        return http_json(CODEX_USAGE_URL, headers)

    try:
        data = call(tokens)
    except urllib.error.HTTPError as e:
        # Only 401 means a bad token; a 403 is usually a bot challenge, and
        # refreshing on it would rotate the login on every poll.
        if e.code != 401:
            raise FetchError("http", str(e.code))
        with codex_lock():
            auth = load_codex_auth()
            # The CLI may have refreshed in the meantime; use its token then.
            if auth["tokens"]["access_token"] == tokens["access_token"]:
                auth = refresh_codex(auth)
            tokens = auth["tokens"]
        try:
            data = call(tokens)
        except urllib.error.HTTPError as e2:
            raise FetchError("http", str(e2.code))

    windows = []
    rl = data.get("rate_limit") or {}
    for slot in ("primary_window", "secondary_window"):
        w = rl.get(slot)
        if not w or w.get("used_percent") is None:
            continue
        seconds = w.get("limit_window_seconds") or 0
        # Classify by window length rather than slot, which has moved before.
        if seconds and seconds <= 6 * 3600:
            key = "session"
        else:
            key = "weekly"
        reset = w.get("reset_at")
        if reset is None and w.get("reset_after_seconds") is not None:
            reset = time.time() + w["reset_after_seconds"]
        windows.append(window(key, w["used_percent"], iso_from_epoch(reset) if reset else None))
    windows.sort(key=lambda w: w["key"] != "session")

    # The API only lists a window once it has started, so an idle 5-hour
    # window is simply absent rather than reported as 0%.
    plan = data.get("plan_type") or (jwt_claims(tokens.get("id_token", "")).get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type")
    return {"plan": CODEX_PLAN_NAMES.get(plan, plan.capitalize()) if plan else None, "windows": windows}


# OpenCode-managed plans (Z.ai GLM Coding Plan and OpenCode Go)
#
# Both keys live in the auth store the OpenCode TUI keeps at
# ~/.local/share/opencode/auth.json, written by `opencode auth login`.


def load_opencode_auth():
    try:
        with open(OPENCODE_AUTH) as f:
            return json.load(f)
    except (OSError, ValueError):
        raise FetchError("opencode_no_login")


def opencode_key(auth, provider, label):
    try:
        return auth[provider]["key"]
    except (KeyError, TypeError):
        # The label names the plan in the error the UI shows, since both
        # keys live in the same file.
        raise FetchError("opencode_no_login", label)


def fetch_zai():
    key = opencode_key(load_opencode_auth(), "zai-coding-plan", "Z.ai GLM")
    try:
        # An internal monitor endpoint the Z.ai console itself uses; not in
        # the official docs, so parse defensively and never assume fields.
        data = http_json(ZAI_USAGE_URL, {
            "Authorization": key,
            "Content-Type": "application/json",
            "User-Agent": "agents-usage-plasmoid",
            "Accept": "application/json",
        })
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("opencode_invalid", "Z.ai GLM")
        raise FetchError("http", str(e.code))

    # A 200 body can still carry a failure envelope; without this check an
    # auth error would silently replace the cached numbers with empty ones.
    if data.get("success") is False or data.get("code") not in (200, None):
        if data.get("code") in (401, 403):
            raise FetchError("opencode_invalid", "Z.ai GLM")
        raise FetchError("http", str(data.get("code")))

    payload = data.get("data") or {}
    windows = []
    for lim in payload.get("limits") or []:
        # TOKENS_LIMIT unit 3 is the 5-hour quota, unit 6 the weekly quota
        # (a 7-day cycle restarting at order time). TIME_LIMIT is the separate
        # monthly quota of the MCP tools, deliberately not shown. "percentage"
        # is the used share — the console meter draws it that way.
        unit = lim.get("unit")
        if lim.get("type") != "TOKENS_LIMIT" or unit not in (3, 6) or lim.get("percentage") is None:
            continue
        reset_ms = lim.get("nextResetTime")
        windows.append(window("session" if unit == 3 else "weekly",
                              lim["percentage"],
                              iso_from_epoch(reset_ms / 1000) if reset_ms else None))
    windows.sort(key=lambda w: w["key"] != "session")

    level = payload.get("level")
    return {"plan": level.capitalize() if level else None, "windows": windows}


def fetch_opencode_go():
    token = opencode_key(load_opencode_auth(), "opencode-go", "OpenCode Go")
    try:
        data = http_json(OPENCODE_GO_USAGE_URL, {
            "Authorization": "Bearer " + token,
            "User-Agent": "agents-usage-plasmoid",
            "Accept": "application/json",
        })
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("opencode_invalid", "OpenCode Go")
        raise FetchError("http", str(e.code))

    usage = data.get("usage") or {}
    windows = []
    for kind, wkey in (("rolling", "session"), ("weekly", "weekly"), ("monthly", "monthly")):
        w = usage.get(kind)
        if not w or w.get("percent") is None:
            continue
        resets = w.get("resetsAt")
        # Normalise the API's trailing "Z": datetime.fromisoformat in
        # expire_window only accepts it from Python 3.11 on.
        if resets and resets.endswith("Z"):
            resets = resets[:-1] + "+00:00"
        windows.append(window(wkey, w["percent"], resets))
    windows.sort(key=lambda w: w["key"] != "session")

    # The endpoint reports no plan name, so no badge is shown.
    return {"plan": None, "windows": windows}


# Main


PROVIDERS = (
    ("claude", "Claude", fetch_claude),
    ("codex", "Codex", fetch_codex),
    ("zai", "Z.ai GLM", fetch_zai),
    ("opencode-go", "OpenCode Go", fetch_opencode_go),
)


def expire_window(w):
    """A cached window whose reset time has passed is known to be unused again."""
    try:
        passed = w.get("resetsAt") and datetime.fromisoformat(w["resetsAt"]).timestamp() < time.time()
    except ValueError:
        passed = False
    return dict(w, usedPercent=0.0, resetsAt=None) if passed else w


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run(entry):
    pid, name, fn = entry
    try:
        return pid, name, fn(), None
    except FetchError as e:
        return pid, name, None, (e.code, e.detail)
    except ValueError:
        # http.client quotes an invalid header value, i.e. the token, in its message.
        return pid, name, None, ("bad_credentials", None)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return pid, name, None, ("network", str(reason))
    except Exception as e:  # never let one provider break the whole popup
        return pid, name, None, ("unknown", "%s: %s" % (type(e).__name__, e))


def main():
    cache = load_cache()
    with ThreadPoolExecutor(len(PROVIDERS)) as pool:
        results = list(pool.map(run, PROVIDERS))

    providers = []
    for pid, name, result, error in results:
        if result is not None:
            cache[pid] = result
            providers.append({"id": pid, "name": name, "ok": True, "stale": False, "errorCode": None, "errorDetail": None, **result})
        elif pid in cache:
            cached = dict(cache[pid], windows=[expire_window(w) for w in cache[pid].get("windows", [])])
            providers.append({"id": pid, "name": name, "ok": False, "stale": True, "errorCode": error[0], "errorDetail": error[1], **cached})
        else:
            providers.append({"id": pid, "name": name, "ok": False, "stale": False, "errorCode": error[0], "errorDetail": error[1], "plan": None, "windows": []})

    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        write_json_atomic(CACHE_FILE, cache)
        # Pre-localisation cache file, superseded by limits-v2.json.
        if os.path.exists(os.path.join(CACHE_DIR, "last.json")):
            os.unlink(os.path.join(CACHE_DIR, "last.json"))
    except OSError:
        pass

    print(json.dumps({"fetchedAt": datetime.now(timezone.utc).isoformat(), "providers": providers}, ensure_ascii=False))


if __name__ == "__main__":
    main()
