#!/usr/bin/env python3
"""Print remaining usage limits for Claude and OpenAI Codex as JSON.

Reads the OAuth credentials that Claude Code and the Codex CLI already store
on disk and asks each service for the current rate-limit windows. Stdlib only,
so the plasmoid needs nothing beyond python3.

Output: {"fetchedAt": ISO, "providers": [Provider, ...]} where a Provider is
{id, name, plan, ok, stale, error, windows: [{key, label, usedPercent, resetsAt}]}.
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
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "agents-usage")
CACHE_FILE = os.path.join(CACHE_DIR, "last.json")

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
# Public OAuth client id of the Codex CLI (the same value the CLI sends).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TIMEOUT = 15


class FetchError(Exception):
    """An error whose message is shown to the user as is (Turkish)."""


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
        return json.load(resp)


def iso_from_epoch(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def window(key, label, used, resets_at):
    return {"key": key, "label": label, "usedPercent": max(0.0, min(100.0, float(used))), "resetsAt": resets_at}


# Claude


def fetch_claude():
    try:
        with open(CLAUDE_CREDS) as f:
            oauth = json.load(f)["claudeAiOauth"]
    except (OSError, ValueError, KeyError):
        raise FetchError("Claude Code girişi bulunamadı (claude ile giriş yap)")

    # The token is only read, never refreshed: Claude Code rotates the refresh
    # token itself and a second writer could log it out.
    if oauth.get("expiresAt", 0) / 1000 < time.time():
        raise FetchError("Claude Code oturumu süresi dolmuş; claude'u bir kez açınca düzelir")

    try:
        data = http_json(CLAUDE_USAGE_URL, {
            "Authorization": "Bearer " + oauth["accessToken"],
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "agents-usage-plasmoid",
            "Accept": "application/json",
        })
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("Claude oturumu geçersiz; claude'u bir kez aç")
        raise FetchError("Claude HTTP %d" % e.code)

    windows = []
    for key, label in (("five_hour", "5 saat"), ("seven_day", "Haftalık")):
        w = data.get(key)
        if w and w.get("utilization") is not None:
            windows.append(window("session" if key == "five_hour" else "weekly", label, w["utilization"], w.get("resets_at")))

    # Per-model weekly caps (e.g. a separate Fable or Opus budget) only appear
    # in the newer "limits" list.
    for lim in data.get("limits") or []:
        if lim.get("kind") != "weekly_scoped":
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if model and lim.get("percent") is not None:
            windows.append(window("weekly_" + model.lower(), "Haftalık · " + model, lim["percent"], lim.get("resets_at")))

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
        raise FetchError("Codex oturumu yenilenemedi; codex login ile tekrar giriş yap")
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
        raise FetchError("Yenilenen Codex oturumu kaydedilemedi (%s); codex login gerekebilir" % e.strerror)
    return current


CODEX_PLAN_NAMES = {"prolite": "Pro Lite", "pro": "Pro", "plus": "Plus", "team": "Team", "business": "Business", "enterprise": "Enterprise", "edu": "Edu", "free": "Free"}


def load_codex_auth():
    try:
        with open(CODEX_AUTH) as f:
            auth = json.load(f)
        auth["tokens"]["access_token"]
        return auth
    except (OSError, ValueError, KeyError, TypeError):
        raise FetchError("Codex ChatGPT girişi bulunamadı (codex login ile giriş yap)")


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
            raise FetchError("Codex HTTP %d" % e.code)
        with codex_lock():
            auth = load_codex_auth()
            # The CLI may have refreshed in the meantime; use its token then.
            if auth["tokens"]["access_token"] == tokens["access_token"]:
                auth = refresh_codex(auth)
            tokens = auth["tokens"]
        try:
            data = call(tokens)
        except urllib.error.HTTPError as e2:
            raise FetchError("Codex HTTP %d" % e2.code)

    windows = []
    rl = data.get("rate_limit") or {}
    for slot in ("primary_window", "secondary_window"):
        w = rl.get(slot)
        if not w or w.get("used_percent") is None:
            continue
        seconds = w.get("limit_window_seconds") or 0
        # Classify by window length rather than slot, which has moved before.
        if seconds and seconds <= 6 * 3600:
            key, label = "session", "5 saat"
        else:
            key, label = "weekly", "Haftalık"
        reset = w.get("reset_at")
        if reset is None and w.get("reset_after_seconds") is not None:
            reset = time.time() + w["reset_after_seconds"]
        windows.append(window(key, label, w["used_percent"], iso_from_epoch(reset) if reset else None))
    windows.sort(key=lambda w: w["key"] != "session")

    # The API only lists a window once it has started, so an idle 5-hour
    # window is simply absent rather than reported as 0%.
    plan = data.get("plan_type") or (jwt_claims(tokens.get("id_token", "")).get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type")
    return {"plan": CODEX_PLAN_NAMES.get(plan, plan.capitalize()) if plan else None, "windows": windows}


# Main


PROVIDERS = (("claude", "Claude", fetch_claude), ("codex", "Codex", fetch_codex))


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
        return pid, name, None, str(e)
    except ValueError:
        # http.client quotes an invalid header value, i.e. the token, in its message.
        return pid, name, None, "Kimlik dosyası okunamadı veya bozuk"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return pid, name, None, "Bağlantı hatası: %s" % reason
    except Exception as e:  # never let one provider break the whole popup
        return pid, name, None, "%s: %s" % (type(e).__name__, e)


def main():
    cache = load_cache()
    with ThreadPoolExecutor(len(PROVIDERS)) as pool:
        results = list(pool.map(run, PROVIDERS))

    providers = []
    for pid, name, result, error in results:
        if result is not None:
            cache[pid] = result
            providers.append({"id": pid, "name": name, "ok": True, "stale": False, "error": None, **result})
        elif pid in cache:
            cached = dict(cache[pid], windows=[expire_window(w) for w in cache[pid].get("windows", [])])
            providers.append({"id": pid, "name": name, "ok": False, "stale": True, "error": error, **cached})
        else:
            providers.append({"id": pid, "name": name, "ok": False, "stale": False, "error": error, "plan": None, "windows": []})

    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        write_json_atomic(CACHE_FILE, cache)
    except OSError:
        pass

    print(json.dumps({"fetchedAt": datetime.now(timezone.utc).isoformat(), "providers": providers}, ensure_ascii=False))


if __name__ == "__main__":
    main()
