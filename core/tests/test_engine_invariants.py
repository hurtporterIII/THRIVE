"""Invariant tests for the core truth engine."""

import unittest

from core.engine import calculate_truth
from core.models import PositionInput


class TruthEngineInvariantTests(unittest.TestCase):
    def test_totals_consistent(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="TEST",
            quantity=10.0,
            cost_basis_per_unit=100.0,
            current_price=150.0,
            days_held=500,
            state_tax_rate=0.05,
        )

        result = calculate_truth(position)

        self.assertAlmostEqual(result.gross_value, 1500.0)
        self.assertAlmostEqual(result.total_gain, 500.0)
        self.assertAlmostEqual(result.federal_tax, 75.0)
        self.assertAlmostEqual(result.state_tax, 25.0)
        self.assertAlmostEqual(result.total_tax, 100.0)
        self.assertAlmostEqual(result.net_liquid_wealth, 1400.0)
        self.assertAlmostEqual(result.efficiency_score, 93.3333333333, places=6)

    def test_percentages_bounded(self) -> None:
        position = PositionInput(
            asset_type="crypto",
            ticker="TEST",
            quantity=3.0,
            cost_basis_per_unit=200.0,
            current_price=250.0,
            days_held=200,
            state_tax_rate=0.0,
        )

        result = calculate_truth(position)

        self.assertGreaterEqual(result.efficiency_score, 0.0)
        self.assertLessEqual(result.efficiency_score, 100.0)

    def test_zero_quantity_outputs(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="ZERO",
            quantity=0.0,
            cost_basis_per_unit=100.0,
            current_price=150.0,
            days_held=10,
        )

        result = calculate_truth(position)

        self.assertAlmostEqual(result.gross_value, 0.0)
        self.assertAlmostEqual(result.total_gain, 0.0)
        self.assertAlmostEqual(result.federal_tax, 0.0)
        self.assertAlmostEqual(result.state_tax, 0.0)
        self.assertAlmostEqual(result.total_tax, 0.0)
        self.assertAlmostEqual(result.net_liquid_wealth, 0.0)
        self.assertAlmostEqual(result.efficiency_score, 100.0)
        self.assertEqual(result.confidence_level, "LOW")
        self.assertTrue(
            any("Zero quantity or price" in note for note in result.assumptions)
        )

    def test_invalid_inputs_fail_loudly(self) -> None:
        invalid_cases = [
            dict(asset_type="bond", quantity=1.0, cost_basis_per_unit=10.0, current_price=10.0, days_held=1),
            dict(asset_type="stock", quantity=-1.0, cost_basis_per_unit=10.0, current_price=10.0, days_held=1),
            dict(asset_type="stock", quantity=1.0, cost_basis_per_unit=-10.0, current_price=10.0, days_held=1),
            dict(asset_type="stock", quantity=1.0, cost_basis_per_unit=10.0, current_price=-10.0, days_held=1),
            dict(asset_type="stock", quantity=1.0, cost_basis_per_unit=10.0, current_price=10.0, days_held=-1),
        ]

        for case in invalid_cases:
            with self.subTest(case=case):
                position = PositionInput(
                    asset_type=case["asset_type"],
                    ticker="BAD",
                    quantity=case["quantity"],
                    cost_basis_per_unit=case["cost_basis_per_unit"],
                    current_price=case["current_price"],
                    days_held=case["days_held"],
                )
                with self.assertRaises(ValueError):
                    calculate_truth(position)


if __name__ == "__main__":
    unittest.main()
