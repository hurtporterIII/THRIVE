"""Thrive Truth Engine
=======================

Deterministic, auditable liquidation math for a single financial position.
This module calculates after-tax, real-world cash value and exposes a simple
CLI for quick inspection without external dependencies.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class PositionInput:
    """Structured input describing one position to liquidate."""

    asset_type: str  # Expected values: "stock" or "crypto"
    ticker: str
    quantity: float
    cost_basis_per_unit: float
    current_price: float
    days_held: int
    filing_status: str = "single"
    state_tax_rate: Optional[float] = None


def determine_tax_classification(days_held: int) -> str:
    """Return tax classification label based on holding period."""

    return "long_term" if days_held > 365 else "short_term"


def determine_federal_rate(tax_classification: str) -> float:
    """Return the assumed federal capital gains rate for the classification."""

    return 0.15 if tax_classification == "long_term" else 0.25


def _validate_position(position: PositionInput) -> None:
    """Basic validation to keep calculations deterministic."""

    if position.asset_type.lower() not in {"stock", "crypto"}:
        raise ValueError("asset_type must be 'stock' or 'crypto'.")
    if position.quantity < 0:
        raise ValueError("quantity must be non-negative.")
    if position.cost_basis_per_unit < 0 or position.current_price < 0:
        raise ValueError("Prices and cost basis must be non-negative.")
    if position.days_held < 0:
        raise ValueError("days_held cannot be negative.")


def _assess_confidence(
    position: PositionInput, state_rate: Optional[float]
) -> Tuple[str, List[str]]:
    """Assign a qualitative confidence level and supporting notes."""

    score = 3
    notes: List[str] = []

    if state_rate is None:
        score -= 1
        notes.append(
            "State tax rate missing; result excludes state-level obligations."
        )

    if position.asset_type.lower() == "crypto":
        score -= 1
        notes.append(
            "Crypto taxation varies; using generalized capital gains assumptions."
        )

    if position.quantity == 0 or position.current_price == 0:
        score -= 1
        notes.append("Zero quantity or price reduces certainty of liquidation math.")

    if score >= 3:
        level = "HIGH"
    elif score == 2:
        level = "MEDIUM"
    else:
        level = "LOW"

    return level, notes


def _tax_classification_countdown(days_held: int) -> Optional[int]:
    """Return days until long-term status when within 60 days, else None."""

    adjusted_days = max(days_held, 0)
    if adjusted_days <= 365:
        days_until = 366 - adjusted_days
        if days_until <= 60:
            return days_until
    return None


def calculate_truth(position: PositionInput) -> Dict[str, object]:
    """Compute after-tax liquidation values and related insights."""

    _validate_position(position)

    gross_value = position.current_price * position.quantity
    total_gain = (position.current_price - position.cost_basis_per_unit) * position.quantity
    tax_classification = determine_tax_classification(position.days_held)
    federal_rate = determine_federal_rate(tax_classification)
    state_rate = position.state_tax_rate

    federal_tax = 0.0
    state_tax = 0.0
    total_tax = 0.0
    loss_offset_value: Optional[float] = None

    assumptions = [
        "Federal long-term capital gains rate assumed at 15%.",
        "Federal short-term capital gains rate assumed at 25%.",
        "Taxes applied only on gains; losses generate no immediate tax bill.",
        "No trading fees, spreads, or liquidity slippage included.",
    ]

    if state_rate is None:
        assumptions.append(
            "No state tax applied because state_tax_rate was not provided."
        )
    else:
        assumptions.append(f"State tax rate applied at {state_rate:.2%} on positive gains.")

    if total_gain > 0:
        federal_tax = total_gain * federal_rate
        state_tax = total_gain * state_rate if state_rate is not None else 0.0
        total_tax = federal_tax + state_tax
    else:
        total_tax = 0.0
        marginal_rate = federal_rate + (state_rate or 0.0)
        loss_offset_value = abs(total_gain) * marginal_rate
        assumptions.append(
            f"Loss offset value estimated using marginal rate of {marginal_rate:.2%}."
        )

    net_liquid_wealth = gross_value - total_tax
    efficiency_score = (net_liquid_wealth / gross_value * 100.0) if gross_value else 0.0

    tax_classification_countdown = _tax_classification_countdown(position.days_held)

    confidence_level, confidence_notes = _assess_confidence(position, state_rate)
    assumptions.extend(confidence_notes)

    result: Dict[str, object] = {
        "gross_value": gross_value,
        "total_gain": total_gain,
        "tax_classification": tax_classification,
        "federal_tax": federal_tax,
        "state_tax": state_tax,
        "total_tax": total_tax,
        "net_liquid_wealth": net_liquid_wealth,
        "efficiency_score": efficiency_score,
        "confidence_level": confidence_level,
        "assumptions": assumptions,
    }

    if loss_offset_value is not None:
        result["loss_offset_value"] = loss_offset_value
    if tax_classification_countdown is not None:
        result["tax_classification_countdown"] = tax_classification_countdown

    return result


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
