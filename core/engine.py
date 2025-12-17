"""Pure computation engine for the Thrive Truth core domain."""

from typing import List, Optional, Tuple

from .models import PositionInput, TruthResult


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

    score = 4
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

    if position.quantity == 0 or position.current_price == 0:
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


def calculate_truth(position: PositionInput) -> TruthResult:
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
    efficiency_score = (
        net_liquid_wealth / gross_value * 100.0 if gross_value > 0 else 100.0
    )

    tax_classification_countdown = _tax_classification_countdown(position.days_held)

    confidence_level, confidence_notes = _assess_confidence(position, state_rate)
    assumptions.extend(confidence_notes)

    return TruthResult(
        gross_value=gross_value,
        total_gain=total_gain,
        tax_classification=tax_classification,
        federal_tax=federal_tax,
        state_tax=state_tax,
        total_tax=total_tax,
        net_liquid_wealth=net_liquid_wealth,
        efficiency_score=efficiency_score,
        confidence_level=confidence_level,
        assumptions=tuple(assumptions),
        loss_offset_value=loss_offset_value,
        tax_classification_countdown=tax_classification_countdown,
    )


calculate_true_wealth = calculate_truth
