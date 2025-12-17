"""Determinism tests for the core engine."""

import os
import unittest

from core.engine import calculate_truth
from core.models import PositionInput


class TruthEngineDeterminismTests(unittest.TestCase):
    def test_same_input_same_output(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="ABC",
            quantity=5.0,
            cost_basis_per_unit=50.0,
            current_price=80.0,
            days_held=400,
            state_tax_rate=0.02,
        )

        first = calculate_truth(position)
        second = calculate_truth(position)

        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_keyword_order_does_not_change_output(self) -> None:
        position_a = PositionInput(
            asset_type="crypto",
            ticker="XYZ",
            quantity=2.5,
            cost_basis_per_unit=100.0,
            current_price=120.0,
            days_held=300,
            state_tax_rate=0.01,
        )
        position_b = PositionInput(
            days_held=300,
            current_price=120.0,
            cost_basis_per_unit=100.0,
            quantity=2.5,
            ticker="XYZ",
            asset_type="crypto",
            state_tax_rate=0.01,
        )

        self.assertEqual(calculate_truth(position_a), calculate_truth(position_b))

    def test_environment_changes_do_not_affect_output(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="ENV",
            quantity=1.0,
            cost_basis_per_unit=10.0,
            current_price=15.0,
            days_held=100,
        )

        baseline = calculate_truth(position).to_dict()
        os.environ["THRIVE_TEST_ENV"] = "changed"
        self.addCleanup(os.environ.pop, "THRIVE_TEST_ENV", None)

        after = calculate_truth(position).to_dict()

        self.assertEqual(baseline, after)


if __name__ == "__main__":
    unittest.main()
