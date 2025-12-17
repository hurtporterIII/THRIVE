"""Unit tests for the Thrive Truth Engine using unittest and stdlib only."""

import unittest

from thrive.truth_engine import PositionInput, calculate_truth


class ThriveTruthEngineTests(unittest.TestCase):
    # Long-term gain scenario should apply long-term federal rate and state tax.
    def test_long_term_gain_stock(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="AAPL",
            quantity=100,
            cost_basis_per_unit=10.0,
            current_price=20.0,
            days_held=400,
            state_tax_rate=0.05,
        )

        result = calculate_truth(position)

        self.assertEqual(result["tax_classification"], "long_term")
        self.assertAlmostEqual(result["gross_value"], 2000.0)
        self.assertAlmostEqual(result["total_gain"], 1000.0)
        self.assertAlmostEqual(result["federal_tax"], 150.0)
        self.assertAlmostEqual(result["state_tax"], 50.0)
        self.assertAlmostEqual(result["total_tax"], 200.0)
        self.assertAlmostEqual(result["net_liquid_wealth"], 1800.0)
        self.assertAlmostEqual(result["efficiency_score"], 90.0)
        self.assertEqual(result["confidence_level"], "HIGH")
        self.assertTrue(
            any("State tax rate applied at 5.00%" in note for note in result["assumptions"])
        )

    # Short-term gain should use short-term federal rate, no countdown, and valid confidence.
    def test_short_term_gain_stock(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="TSLA",
            quantity=20,
            cost_basis_per_unit=50.0,
            current_price=70.0,
            days_held=200,
        )

        result = calculate_truth(position)

        self.assertEqual(result["tax_classification"], "short_term")
        self.assertAlmostEqual(result["gross_value"], 1400.0)
        self.assertAlmostEqual(result["total_gain"], 400.0)
        self.assertAlmostEqual(result["federal_tax"], 100.0)
        self.assertAlmostEqual(result["state_tax"], 0.0)
        self.assertAlmostEqual(result["total_tax"], 100.0)
        self.assertAlmostEqual(result["net_liquid_wealth"], 1300.0)
        self.assertAlmostEqual(result["efficiency_score"], 92.8571428571, places=6)
        self.assertNotIn("tax_classification_countdown", result)
        self.assertIn(result["confidence_level"], {"MEDIUM", "HIGH"})
        self.assertTrue(
            any("No state tax applied" in note for note in result["assumptions"])
        )

    # Loss case should zero out tax, surface loss offset value, and keep efficiency at 100%.
    def test_loss_case_stock(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="MSFT",
            quantity=10,
            cost_basis_per_unit=100.0,
            current_price=80.0,
            days_held=500,
            state_tax_rate=0.05,
        )

        result = calculate_truth(position)

        self.assertEqual(result["tax_classification"], "long_term")
        self.assertAlmostEqual(result["total_gain"], -200.0)
        self.assertAlmostEqual(result["federal_tax"], 0.0)
        self.assertAlmostEqual(result["state_tax"], 0.0)
        self.assertAlmostEqual(result["total_tax"], 0.0)
        self.assertAlmostEqual(result["gross_value"], 800.0)
        self.assertAlmostEqual(result["net_liquid_wealth"], 800.0)
        self.assertAlmostEqual(result["efficiency_score"], 100.0)
        self.assertIn("loss_offset_value", result)
        self.assertAlmostEqual(result["loss_offset_value"], 40.0)
        self.assertEqual(result["confidence_level"], "HIGH")
        self.assertTrue(
            any("Loss offset value estimated" in note for note in result["assumptions"])
        )

    # Countdown should appear when within 60 days of long-term status.
    def test_tax_classification_countdown(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="META",
            quantity=5,
            cost_basis_per_unit=10.0,
            current_price=12.0,
            days_held=350,
            state_tax_rate=0.03,
        )

        result = calculate_truth(position)

        self.assertEqual(result["tax_classification"], "short_term")
        self.assertIn("tax_classification_countdown", result)
        self.assertEqual(result["tax_classification_countdown"], 16)
        self.assertNotIn("recommendation", " ".join(result["assumptions"]).lower())

    # Zero quantity should not crash, keep monetary outputs at zero, and mark confidence as LOW.
    def test_zero_quantity_edge_case(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="NFLX",
            quantity=0,
            cost_basis_per_unit=400.0,
            current_price=450.0,
            days_held=100,
        )

        result = calculate_truth(position)

        self.assertAlmostEqual(result["gross_value"], 0.0)
        self.assertAlmostEqual(result["total_gain"], 0.0)
        self.assertAlmostEqual(result["federal_tax"], 0.0)
        self.assertAlmostEqual(result["state_tax"], 0.0)
        self.assertAlmostEqual(result["total_tax"], 0.0)
        self.assertAlmostEqual(result["net_liquid_wealth"], 0.0)
        self.assertAlmostEqual(result["efficiency_score"], 100.0)
        self.assertNotIn("tax_classification_countdown", result)
        self.assertEqual(result["confidence_level"], "LOW")
        self.assertTrue(
            any("Zero quantity or price reduces certainty" in note for note in result["assumptions"])
        )

    # Crypto with no state tax should apply federal short-term rate and return MEDIUM confidence.
    def test_crypto_gain_no_state_tax(self) -> None:
        position = PositionInput(
            asset_type="crypto",
            ticker="ETH",
            quantity=2.0,
            cost_basis_per_unit=1000.0,
            current_price=1500.0,
            days_held=200,
        )

        result = calculate_truth(position)

        self.assertEqual(result["tax_classification"], "short_term")
        self.assertAlmostEqual(result["gross_value"], 3000.0)
        self.assertAlmostEqual(result["total_gain"], 1000.0)
        self.assertAlmostEqual(result["federal_tax"], 250.0)
        self.assertAlmostEqual(result["state_tax"], 0.0)
        self.assertAlmostEqual(result["total_tax"], 250.0)
        self.assertAlmostEqual(result["net_liquid_wealth"], 2750.0)
        self.assertAlmostEqual(result["efficiency_score"], 91.6666666667, places=6)
        self.assertNotIn("tax_classification_countdown", result)
        self.assertEqual(result["confidence_level"], "MEDIUM")
        self.assertTrue(
            any("No state tax applied" in note for note in result["assumptions"])
        )
        self.assertTrue(
            any("Crypto taxation varies" in note for note in result["assumptions"])
        )


if __name__ == "__main__":
    unittest.main()
