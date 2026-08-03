import unittest
import pandas as pd

import nse500_screener as screener


class WatchlistTickerFallbackTests(unittest.TestCase):
    def test_download_ohlcv_history_tries_fallback_ticker(self):
        seen = []

        def fake_download(*args, **kwargs):
            ticker = kwargs.get("tickers") or args[0]
            seen.append(ticker)
            if ticker == "SETFNIF50.NS":
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            if ticker == "SETFNIF50":
                return pd.DataFrame(
                    {
                        "Open": [100],
                        "High": [101],
                        "Low": [99],
                        "Close": [100.5],
                        "Volume": [5000],
                    },
                    index=pd.date_range("2024-01-01", periods=1, freq="D"),
                )
            raise AssertionError(f"unexpected ticker {ticker}")

        frame = screener._download_ohlcv_history(
            "SETFNIF50",
            suffix=".NS",
            period="2y",
            interval="1wk",
            download_fn=fake_download,
        )

        self.assertFalse(frame.empty)
        self.assertEqual(seen[0], "SETFNIF50.NS")
        self.assertEqual(seen[1], "SETFNIF50")


if __name__ == "__main__":
    unittest.main()
