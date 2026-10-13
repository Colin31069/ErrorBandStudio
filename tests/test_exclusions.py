from __future__ import annotations

import unittest

from skin_analysis.exclusions import current_excluded_samples, max_excluded_samples, unhonored_excluded_samples
from skin_analysis.models import ExcludedSample


class ExclusionPolicyTests(unittest.TestCase):
    def test_max_excluded_samples_uses_twenty_percent_floor_with_minimum_n(self) -> None:
        cases = {
            0: 0,
            4: 0,
            5: 1,
            9: 1,
            10: 2,
            14: 2,
            15: 3,
        }

        for sample_count, expected in cases.items():
            with self.subTest(sample_count=sample_count):
                self.assertEqual(max_excluded_samples(sample_count), expected)

    def test_current_excluded_samples_allows_one_dixon_exception_for_small_n(self) -> None:
        files = ["1.xlsx", "2.xlsx", "3.xlsx"]
        excluded = [
            ExcludedSample(file_name="2.xlsx", reason="Dixon Q", method="dixon_q"),
            ExcludedSample(file_name="3.xlsx", reason="manual"),
        ]

        current = current_excluded_samples(excluded, files)

        self.assertEqual(current, [ExcludedSample(file_name="2.xlsx", reason="Dixon Q", method="dixon_q")])

    def test_current_excluded_samples_rejects_non_dixon_exclusion_for_small_n(self) -> None:
        files = ["1.xlsx", "2.xlsx", "3.xlsx"]
        excluded = [ExcludedSample(file_name="2.xlsx", reason="manual")]

        self.assertEqual(current_excluded_samples(excluded, files), [])


    def test_recorded_exclusion_is_not_silently_included_after_sample_count_shrinks(self) -> None:
        excluded = [ExcludedSample(file_name="2.xlsx", reason="bad contact")]
        original_files = [f"{i}.xlsx" for i in range(1, 6)]
        self.assertEqual(unhonored_excluded_samples(excluded, original_files), [])
        remaining_files = original_files[:4]
        self.assertEqual(current_excluded_samples(excluded, remaining_files), [])
        self.assertEqual(unhonored_excluded_samples(excluded, remaining_files), excluded)
        self.assertEqual(unhonored_excluded_samples(excluded, ["1.xlsx", "3.xlsx"]), [])

if __name__ == "__main__":
    unittest.main()
