import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'package/contents/code/token_stats.py'
spec = importlib.util.spec_from_file_location('token_stats', PATH)
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


def turn(model='grok-example', input=100, cached=40, output=20):
    return {'turnNumber': 1, 'endedAt': '2026-11-10T12:00:00Z', 'primaryModelId': model,
            'modelUsage': {model: {'inputTokens': input, 'cachedReadTokens': cached,
                                  'cacheCreationTokens': 0, 'outputTokens': output,
                                  'reasoningTokens': 15, 'totalTokens': input + output}}}


class GrokTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'sessions' / 'workspace' / 'session-one' / 'usage.json'
        self.path.parent.mkdir(parents=True)
        self.write([turn()])

    def write(self, turns):
        self.path.write_text(json.dumps({'sessionId': 'session-one', 'turns': turns}))

    def test_cached_input_and_reasoning_are_not_counted_twice(self):
        data = stats.parse_grok(self.path)
        self.assertEqual(data, {'2026-11-10': {'grok-example': [60, 20, 40, 0, 0]}})
        self.assertEqual(sum(data['2026-11-10']['grok-example']), 120)

    def test_multiple_models_in_one_turn(self):
        t = turn()
        t['modelUsage'].update(turn('grok-other', 200, 10, 30)['modelUsage'])
        self.write([t])
        data = stats.parse_grok(self.path)['2026-11-10']
        self.assertEqual(sum(data['grok-other']), 230)
        self.assertEqual(sum(map(sum, data.values())), 350)

    def test_turn_total_without_model_breakdown_stays_unattributed(self):
        t = turn()
        t.update(t.pop('modelUsage')['grok-example'])
        self.write([t])
        self.assertEqual(sum(stats.parse_grok(self.path)['2026-11-10']['grok']), 120)

    def test_invalid_counts_are_not_added(self):
        t = turn()
        t['modelUsage']['grok-example']['cachedReadTokens'] = 101
        self.write([t, {'endedAt': 'invalid'}, turn('valid')])
        self.assertEqual(list(stats.parse_grok(self.path)['2026-11-10']), ['valid'])

    def test_discovery_excludes_artifacts_and_honors_session_layout(self):
        artifact = self.path.parent / 'artifacts' / 'example' / 'usage.json'
        artifact.parent.mkdir(parents=True)
        artifact.write_text(self.path.read_text())
        with patch.object(stats, 'GROK_DIR', str(self.root / 'sessions')), patch.object(stats, 'CLAUDE_DIR', str(self.root / 'absent')), patch.object(stats, 'CODEX_DIRS', []):
            found = stats.list_files(0)
        self.assertEqual(list(found), [str(self.path)])
        self.assertEqual(found[str(self.path)][0], 'grok')

    def test_rescans_and_rewritten_snapshot_replace_totals(self):
        cache = {'files': {}}
        def found(_):
            st = self.path.stat()
            return {str(self.path): ('grok', st.st_size, st.st_mtime_ns)}
        with patch.object(stats, 'list_files', side_effect=found), patch.object(stats, 'ProcessPoolExecutor', ThreadPoolExecutor):
            stats.update(cache, date(2026, 11, 10))
            stats.update(cache, date(2026, 11, 10))
            self.assertEqual(sum(cache['files'][str(self.path)]['contrib']['2026-11-10']['grok-example']), 120)
            self.write([turn(input=300)])
            stats.update(cache, date(2026, 11, 10))
            self.assertEqual(sum(cache['files'][str(self.path)]['contrib']['2026-11-10']['grok-example']), 320)
            self.write([])
            stats.update(cache, date(2026, 11, 10))
            self.assertEqual(cache['files'][str(self.path)]['contrib'], {})

    def test_incomplete_snapshot_keeps_cache_and_is_retried(self):
        cache = {'files': {}}
        def found(_):
            st = self.path.stat()
            return {str(self.path): ('grok', st.st_size, st.st_mtime_ns)}
        with patch.object(stats, 'list_files', side_effect=found), patch.object(stats, 'ProcessPoolExecutor', ThreadPoolExecutor):
            stats.update(cache, date(2026, 11, 10))
            self.path.write_text('{')
            stats.update(cache, date(2026, 11, 10))
            self.assertEqual(sum(cache['files'][str(self.path)]['contrib']['2026-11-10']['grok-example']), 120)
            self.write([turn(input=400)])
            stats.update(cache, date(2026, 11, 10))
            self.assertEqual(sum(cache['files'][str(self.path)]['contrib']['2026-11-10']['grok-example']), 420)

    def test_periods_models_and_unknown_cost(self):
        cache = {'files': {'fixture': {'kind': 'grok', 'contrib': stats.parse_grok(self.path)}}}
        data = stats.summarise(cache, date(2026, 11, 10), lambda _: None, 'test')
        self.assertEqual(data['periods']['7']['grok'], 120)
        self.assertEqual(data['periods']['7']['unpriced']['grok'], 120)
        self.assertIsNone(data['periods']['7']['models'][0]['cost'])
        self.assertEqual(data['daily'][-1]['grok'], 120)
        data = stats.summarise(cache, date(2026, 11, 10), lambda _: (2, 6, .5, 0, 0), 'test')
        self.assertEqual(data['periods']['7']['cost']['grok'], .0003)
