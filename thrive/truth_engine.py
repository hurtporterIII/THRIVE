"""Thrive Truth Engine
=======================

Deterministic, auditable liquidation math for a single financial position.
This module calculates after-tax, real-world cash value and exposes a simple
CLI for quick inspection without external dependencies.
"""

from typing import Dict

from core.engine import calculate_truth as _calculate_truth
from core.engine import determine_federal_rate, determine_tax_classification
from core.models import PositionInput


def calculate_truth(position: PositionInput) -> Dict[str, object]:
    """Compute after-tax liquidation values and related insights."""

    return _calculate_truth(position).to_dict()


calculate_true_wealth = calculate_truth


def _format_currency(value: float) -> str:
    """Format a float as currency for console output."""

    return f"${value:,.2f}"


def print_report(position: PositionInput, result: Dict[str, object]) -> None:
    """Render a human-readable liquidation report."""

    gain_label = "gain" if result["total_gain"] >= 0 else "loss"
    tax_class = result["tax_classification"].replace("_", "-")

    print("Thrive Truth Engine - Liquidation Reality Check")
    print("=" * 60)
    print(f"Position: {position.ticker} ({position.asset_type})")
    print(f"Quantity: {position.quantity} units")
    print(f"Current price: {_format_currency(position.current_price)}")
    print(f"Cost basis per unit: {_format_currency(position.cost_basis_per_unit)}")
    print()
    print(f"Gross liquidation value: {_format_currency(result['gross_value'])}")
    print(
        f"Total {gain_label}: {_format_currency(result['total_gain'])} "
        f"({tax_class})"
    )
    print(f"Federal tax: {_format_currency(result['federal_tax'])}")
    print(f"State tax: {_format_currency(result['state_tax'])}")
    print(f"Total estimated tax: {_format_currency(result['total_tax'])}")
    print(f"Net liquid wealth: {_format_currency(result['net_liquid_wealth'])}")
    print(f"Efficiency score: {result['efficiency_score']:.2f}%")
    print(f"Confidence: {result['confidence_level']}")

    if "tax_classification_countdown" in result:
        print(
            f"Timing note: Tax classification changes in "
            f"{result['tax_classification_countdown']} days."
        )

    if "loss_offset_value" in result:
        print(
            f"Loss offset insight: Potential future tax offset worth "
            f"{_format_currency(result['loss_offset_value'])}."
        )

    print("\nAssumptions and caveats:")
    for assumption in result["assumptions"]:
        print(f"- {assumption}")


def run_example() -> None:
    """Execute a realistic example when run as a script."""

    example_position = PositionInput(
        asset_type="stock",
        ticker="AAPL",
        quantity=50,
        cost_basis_per_unit=120.0,
        current_price=180.0,
        days_held=340,
        filing_status="single",
        state_tax_rate=0.05,
    )

    result = calculate_truth(example_position)
    print_report(example_position, result)


if __name__ == "__main__":
    run_example()
