import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch


HELPER = Path(__file__).resolve().parents[1] / "package/contents/code/fetch_usage.py"
spec = importlib.util.spec_from_file_location("fetch_usage", HELPER)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class GrokTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name) / "auth.json"
        self.auth.write_text(json.dumps({"https://auth.x.ai::test-client": {
            "key": "fake-access-token", "expires_at": "2100-01-01T00:00:00Z",
            "refresh_token": "fake-refresh-token",
        }}))
        p = patch.object(usage, "GROK_AUTH", str(self.auth))
        p.start()
        self.addCleanup(p.stop)

    def billing(self, used=13, period="WEEKLY"):
        return {"config": {"creditUsagePercent": used, "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_" + period,
            "end": "2100-01-08T00:00:00Z",
        }}}

    def test_weekly_quota_plan_and_read_only_credentials(self):
        before = self.auth.read_bytes()
        with patch.object(usage, "http_json", side_effect=[self.billing(), {"subscription_tier_display": "SuperGrok"}]) as http:
            result = usage.fetch_grok()
        self.assertEqual(result, {"plan": "SuperGrok", "windows": [{
            "key": "weekly", "model": None, "usedPercent": 13,
            "resetsAt": "2100-01-08T00:00:00Z",
        }]})
        self.assertEqual(self.auth.read_bytes(), before)
        self.assertEqual([call.args[0] for call in http.call_args_list], [usage.GROK_USAGE_URL, usage.GROK_SETTINGS_URL])
        self.assertEqual(http.call_args_list[0].args[1]["Authorization"], "Bearer fake-access-token")
        self.assertEqual(http.call_args_list[0].args[1]["x-xai-token-auth"], "xai-grok-cli")

    def test_monthly_and_explicit_zero(self):
        with patch.object(usage, "http_json", side_effect=[self.billing(0, "MONTHLY"), {}]):
            result = usage.fetch_grok()
        self.assertEqual(result["windows"][0]["key"], "monthly")
        self.assertEqual(result["windows"][0]["usedPercent"], 0)

    def test_unknown_period_and_reset_fallback(self):
        data = {"config": {"creditUsagePercent": 20, "billingPeriodEnd": "2100-02-01T00:00:00Z"}}
        with patch.object(usage, "http_json", side_effect=[data, {}]):
            result = usage.fetch_grok()
        self.assertEqual(result["windows"][0]["key"], "credits")
        self.assertEqual(result["windows"][0]["resetsAt"], "2100-02-01T00:00:00Z")

    def test_missing_or_invalid_percentage_is_unknown(self):
        for value in [None, True, "13", float("nan"), float("inf")]:
            with self.subTest(value=value), patch.object(usage, "http_json", return_value=self.billing(value)):
                with self.assertRaises(usage.FetchError) as error:
                    usage.fetch_grok()
                self.assertEqual(error.exception.code, "grok_no_usage")

    def test_settings_failure_preserves_quota(self):
        for settings in [urllib.error.URLError("offline"), None, []]:
            with self.subTest(settings=settings), patch.object(usage, "http_json", side_effect=[self.billing(), settings]):
                result = usage.fetch_grok()
            self.assertIsNone(result["plan"])
            self.assertEqual(result["windows"][0]["usedPercent"], 13)

    def test_missing_corrupt_and_unrecognized_login(self):
        for content in ["not json", "[]", "{}", '{"unrelated": {"key": "fake-token"}}']:
            with self.subTest(content=content):
                self.auth.write_text(content)
                with patch.object(usage, "http_json") as http:
                    with self.assertRaises(usage.FetchError) as error:
                        usage.fetch_grok()
                    self.assertEqual(error.exception.code, "grok_no_login")
                    http.assert_not_called()
        self.auth.unlink()
        with self.assertRaises(usage.FetchError) as error:
            usage.fetch_grok()
        self.assertEqual(error.exception.code, "grok_no_login")

    def test_expired_login_is_not_sent_or_refreshed(self):
        auth = json.loads(self.auth.read_text())
        next(iter(auth.values()))["expires_at"] = "2000-01-01T00:00:00Z"
        self.auth.write_text(json.dumps(auth))
        before = self.auth.read_bytes()
        with patch.object(usage, "http_json") as http:
            with self.assertRaises(usage.FetchError) as error:
                usage.fetch_grok()
            self.assertEqual(error.exception.code, "grok_expired")
            http.assert_not_called()
        self.assertEqual(self.auth.read_bytes(), before)

    def test_current_scope_precedes_legacy(self):
        auth = {"https://accounts.x.ai/sign-in": {"key": "fake-legacy", "expires_at": "2100-01-01T00:00:00Z"}}
        auth.update(json.loads(self.auth.read_text()))
        self.auth.write_text(json.dumps(auth))
        with patch.object(usage, "http_json", side_effect=[self.billing(), {}]) as http:
            usage.fetch_grok()
        self.assertEqual(http.call_args_list[0].args[1]["Authorization"], "Bearer fake-access-token")

    def test_legacy_scope(self):
        self.auth.write_text(json.dumps({"https://accounts.x.ai/sign-in": {
            "key": "fake-legacy", "expires_at": "2100-01-01T00:00:00+00:00",
        }}))
        with patch.object(usage, "http_json", side_effect=[self.billing(), {}]):
            self.assertEqual(usage.fetch_grok()["windows"][0]["usedPercent"], 13)

    def test_auth_http_errors_are_localized_without_response_body(self):
        for status, code in [(401, "grok_invalid"), (403, "grok_invalid"), (500, "http")]:
            error = urllib.error.HTTPError(usage.GROK_USAGE_URL, status, "private response", {}, None)
            self.addCleanup(error.close)
            with self.subTest(status=status), patch.object(usage, "http_json", side_effect=error):
                result = usage.run(("grok", "Grok", usage.fetch_grok))
            self.assertEqual(result[3], (code, str(status) if code == "http" else None))

    def test_grok_home_override(self):
        env = dict(os.environ, GROK_HOME=self.temp.name)
        code = "import runpy; print(runpy.run_path(%r)['GROK_AUTH'])" % str(HELPER)
        result = subprocess.run([sys.executable, "-I", "-c", code], env=env, check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), str(self.auth))

    def test_failed_grok_fetch_keeps_cached_quota_and_other_providers(self):
        cache = {"grok": {"plan": "SuperGrok", "windows": [usage.window("weekly", 13, "2100-01-08T00:00:00Z")]}}
        providers = (("claude", "Claude", lambda: {"plan": "Max", "windows": []}),
                     ("codex", "Codex", lambda: {"plan": "Plus", "windows": []}),
                     ("grok", "Grok", usage.fetch_grok))
        with patch.object(usage, "PROVIDERS", providers), patch.object(usage, "load_cache", return_value=cache), \
                patch.object(usage, "CACHE_DIR", self.temp.name), patch.object(usage, "CACHE_FILE", str(Path(self.temp.name) / "cache.json")), \
                patch.object(usage, "http_json", side_effect=urllib.error.URLError("offline")), contextlib.redirect_stdout(io.StringIO()) as out:
            usage.main()
        rows = json.loads(out.getvalue())["providers"]
        self.assertTrue(all(row["ok"] for row in rows[:2]))
        self.assertTrue(rows[2]["stale"])
        self.assertFalse(rows[2]["ok"])
        self.assertEqual(rows[2]["windows"][0]["usedPercent"], 13)


if __name__ == "__main__":
    unittest.main()
