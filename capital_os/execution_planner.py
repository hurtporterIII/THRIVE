"""Deterministic execution planner that produces unsigned plans."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from .capital_model import CapitalSnapshot


@dataclass(frozen=True)
class ExecutionIntent:
    action: str
    asset_code: str
    quantity: Decimal
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionStep:
    sequence: int
    action: str
    asset_code: str
    quantity: Decimal
    description: str


@dataclass(frozen=True)
class ExecutionPlan:
    intent: ExecutionIntent
    steps: Tuple[ExecutionStep, ...]
    assumptions: Tuple[str, ...]
    estimated_cost: Decimal
    failure_modes: Tuple[str, ...]


class ExecutionPlanner:
    """Builds explainable, reproducible plans without executing them."""

    def plan(self, intent: ExecutionIntent, snapshot: CapitalSnapshot) -> ExecutionPlan:
        step_description = f"{intent.action} {intent.quantity} {intent.asset_code}"
        steps = (
            ExecutionStep(
                sequence=1,
                action=intent.action,
                asset_code=intent.asset_code,
                quantity=intent.quantity,
                description=step_description,
            ),
        )
        assumptions = (
            "No fees, slippage, or taxes included.",
            "Execution venues and routes are outside of this planner.",
        )
        failure_modes = (
            "Insufficient balance or liquidity for the requested action.",
            "Execution venue unavailable or rejects the request.",
        )
        return ExecutionPlan(
            intent=intent,
            steps=steps,
            assumptions=assumptions,
            estimated_cost=Decimal("0"),
            failure_modes=failure_modes,
        )
