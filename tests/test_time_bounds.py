import unittest

import pandas as pd

from runtime.task_runtime import time_bounds


class TimeBoundsTests(unittest.TestCase):
    def test_supported_granularities_use_inclusive_end_period(self) -> None:
        cases = [
            ({"start": "2026-06-01", "end": "2026-06-03", "granularity": "day"}, "2026-06-01", "2026-06-04", "2026-06-01_2026-06-03"),
            ({"start": "2026-06", "end": "2026-07", "granularity": "month"}, "2026-06-01", "2026-08-01", "2026-06_2026-07"),
            ({"start": "2025", "end": "2026", "granularity": "year"}, "2025-01-01", "2027-01-01", "2025_2026"),
        ]
        for spec, expected_start, expected_end, expected_slug in cases:
            with self.subTest(spec=spec):
                start, end, slug = time_bounds(spec)
                self.assertEqual(start, pd.Timestamp(expected_start))
                self.assertEqual(end, pd.Timestamp(expected_end))
                self.assertEqual(slug, expected_slug)

    def test_unsupported_or_reversed_ranges_fail(self) -> None:
        with self.assertRaises(ValueError):
            time_bounds({"start": "2026-01", "end": "2026-02", "granularity": "quarter"})
        with self.assertRaises(ValueError):
            time_bounds({"start": "2026-07", "end": "2026-06", "granularity": "month"})


if __name__ == "__main__":
    unittest.main()
