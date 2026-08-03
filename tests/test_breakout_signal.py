import unittest
import pandas as pd

import nse500_screener as screener


class BreakoutSignalTests(unittest.TestCase):
    def test_breakout_signal_classification(self):
        history = pd.DataFrame(
            {
                "Open": [100 + i for i in range(21)],
                "High": [102 + i for i in range(21)],
                "Low": [99 + i for i in range(21)],
                "Close": [101 + i for i in range(21)],
                "Volume": [1000 + i * 100 for i in range(21)],
            }
        )
        history.loc[len(history) - 1, "Close"] = 130
        history.loc[len(history) - 1, "High"] = 135
        history.loc[len(history) - 1, "Volume"] = 5000
        signal, score = screener.classify_breakout_retest_signal(history)
        self.assertEqual(signal, "BREAKOUT")
        self.assertGreaterEqual(score, 0)


if __name__ == "__main__":
    unittest.main()
