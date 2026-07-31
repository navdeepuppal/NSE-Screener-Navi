import tempfile
import unittest
from datetime import date
from pathlib import Path

import nse500_screener as screener


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        screener.CACHE_DIR = Path(self.tempdir.name)
        screener.CACHE_DIR.mkdir(exist_ok=True)
        screener.CACHE_FILE = screener.CACHE_DIR / "daily_result.pkl"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_daily_cache_round_trip(self):
        payload = {"df_all": "dummy", "date": date.today().isoformat()}
        screener.save_daily_result(payload)
        self.assertEqual(screener.load_daily_result(), payload)


if __name__ == "__main__":
    unittest.main()
