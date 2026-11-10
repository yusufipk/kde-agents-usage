import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'package/contents/code/prices.py'
spec = importlib.util.spec_from_file_location('prices', PATH)
prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prices)


class GrokPriceTests(unittest.TestCase):
    def test_xai_provider_prefix_and_cached_input(self):
        rates = prices._from_litellm({'xai/grok-example': {
            'litellm_provider': 'xai', 'input_cost_per_token': 2e-6,
            'output_cost_per_token': 6e-6, 'cache_read_input_token_cost': .5e-6,
        }})
        self.assertEqual(rates['grok-example'], [2, 6, .5, 0, 0])
        self.assertAlmostEqual(prices.cost(rates['grok-example'], [100, 20, 200, 0, 0]), .00042)

    def test_missing_xai_price_remains_unknown(self):
        with tempfile.TemporaryDirectory() as cache, patch.object(prices, '_fetch', return_value={}):
            lookup, _ = prices.load(cache)
            self.assertIsNone(lookup('grok-unknown'))

    def test_old_price_cache_refreshes_for_xai(self):
        import json
        import time
        with tempfile.TemporaryDirectory() as cache:
            (Path(cache) / 'prices.json').write_text(json.dumps({'fetchedAt': time.time(), 'triedAt': time.time(), 'models': {}}))
            with patch.object(prices, '_fetch', return_value={'grok-example': [2, 6, .5, 0, 0]}) as fetch:
                lookup, _ = prices.load(cache)
            fetch.assert_called_once()
            self.assertEqual(lookup('grok-example'), (2, 6, .5, 0, 0))
