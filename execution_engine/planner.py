"""Deterministic execution plan builder with validation."""

from typing import Dict, Iterable, List, Tuple

from .models import (
    ActionType,
    CapitalState,
    ExecutionIntent,
    ExecutionPlan,
    ExecutionStep,
    SignatureRequirement,
)


class PlanValidationError(ValueError):
    """Raised when an execution plan violates hard validation rules."""


class UnimplementedIntentError(ValueError):
    """Raised when the planner cannot handle the provided intent."""


class ExecutionPlanner:
    """Builds explainable, unsigned execution plans."""

    def plan(self, intent: ExecutionIntent, capital_state: CapitalState) -> ExecutionPlan:
        if not isinstance(intent.action_type, ActionType):
            raise UnimplementedIntentError("Unsupported action type.")

        if intent.action_type == ActionType.HOLD:
            steps = (
                ExecutionStep(
                    sequence=1,
                    action_type=ActionType.HOLD,
                    from_asset=intent.from_asset,
                    to_asset=intent.from_asset,
                    amount=0.0,
                    rationale="Hold position; no execution required.",
                ),
            )
            assumptions = (
                "Hold intent produces no execution steps beyond acknowledgement.",
                "Capital state is a snapshot and may be stale.",
            )
            failure_modes = ("Intent rejected by policy gate.",)
        elif intent.action_type in (ActionType.SWAP, ActionType.TRANSFER):
            rationale = (
                f"{intent.action_type.value} {intent.amount} "
                f"{intent.from_asset} to {intent.to_asset}."
            )
            steps = (
                ExecutionStep(
                    sequence=1,
                    action_type=intent.action_type,
                    from_asset=intent.from_asset,
                    to_asset=intent.to_asset,
                    amount=intent.amount,
                    rationale=rationale,
                ),
            )
            assumptions = (
                "No fees, slippage, or taxes included.",
                "Execution venues and routing are outside this engine.",
                "Capital state is a snapshot and may be stale.",
            )
            failure_modes = (
                "Insufficient balance for requested quantity.",
                "Required signer unavailable or rejects the request.",
            )
        else:
            raise UnimplementedIntentError("Unsupported action type.")

        required_signatures = _derive_required_signatures(steps)
        plan = ExecutionPlan(
            intent=intent,
            capital_state=capital_state,
            steps=_ordered_steps(steps),
            assumptions=_ordered_strings(assumptions),
            failure_modes=_ordered_strings(failure_modes),
            required_signatures=required_signatures,
            estimated_cost=0.0,
        )
        validate_plan(plan)
        return plan


def validate_plan(plan: ExecutionPlan) -> None:
    if not plan.steps:
        raise PlanValidationError("Plan must include at least one step.")
    if not plan.assumptions:
        raise PlanValidationError("Plan must include assumptions.")
    if not plan.failure_modes:
        raise PlanValidationError("Plan must include failure modes.")
    if plan.estimated_cost is None:
        raise PlanValidationError("Plan must include an estimated cost.")

    _validate_steps(plan.steps)
    _validate_deterministic_order(plan.steps, plan.assumptions, plan.failure_modes)
    _validate_signatures(plan.steps, plan.required_signatures)
    _validate_capital_conservation(plan)
    _validate_no_hidden_execution_knowledge(plan)


def _validate_steps(steps: Tuple[ExecutionStep, ...]) -> None:
    for step in steps:
        if not isinstance(step.action_type, ActionType):
            raise PlanValidationError("Step action_type must be a defined enum.")
        if not step.rationale:
            raise PlanValidationError("Step rationale must be non-empty.")
        if step.action_type == ActionType.HOLD:
            if step.from_asset != step.to_asset:
                raise PlanValidationError("HOLD steps must keep the same asset.")
            if step.amount != 0.0:
                raise PlanValidationError("HOLD steps must not move capital.")
        else:
            if step.from_asset == step.to_asset:
                raise PlanValidationError("from_asset must differ from to_asset.")
            if step.amount <= 0:
                raise PlanValidationError("Step amount must be positive.")


def _validate_deterministic_order(
    steps: Tuple[ExecutionStep, ...],
    assumptions: Tuple[str, ...],
    failure_modes: Tuple[str, ...],
) -> None:
    sequences = [step.sequence for step in steps]
    if sequences != sorted(sequences):
        raise PlanValidationError("Steps must be ordered by sequence.")
    if list(assumptions) != sorted(assumptions):
        raise PlanValidationError("Assumptions must be deterministically ordered.")
    if list(failure_modes) != sorted(failure_modes):
        raise PlanValidationError("Failure modes must be deterministically ordered.")


def _validate_signatures(
    steps: Tuple[ExecutionStep, ...],
    required_signatures: Tuple[SignatureRequirement, ...],
) -> None:
    derived = _derive_required_signatures(steps)
    if derived and not required_signatures:
        raise PlanValidationError("Required signatures missing for ownership changes.")
    if not derived and required_signatures:
        raise PlanValidationError("HOLD plans must not require signatures.")
    if derived != required_signatures:
        raise PlanValidationError("Required signatures must be derived from steps.")


def _validate_capital_conservation(plan: ExecutionPlan) -> None:
    total_before = sum(exposure.quantity for exposure in plan.capital_state.exposures)
    deltas = _apply_steps(plan.steps)
    total_after = total_before + sum(deltas.values())
    if round(total_before - plan.estimated_cost, 12) != round(total_after, 12):
        raise PlanValidationError("Capital is not conserved.")


def _apply_steps(steps: Tuple[ExecutionStep, ...]) -> Dict[str, float]:
    deltas: Dict[str, float] = {}
    for step in steps:
        if step.action_type == ActionType.HOLD:
            continue
        deltas[step.from_asset] = deltas.get(step.from_asset, 0.0) - step.amount
        deltas[step.to_asset] = deltas.get(step.to_asset, 0.0) + step.amount
    return deltas


def _validate_no_hidden_execution_knowledge(plan: ExecutionPlan) -> None:
    forbidden = ("chain", "gas", "gwei", "wei", "protocol", "wallet", "address", "0x")
    for value in _iter_plan_strings(plan):
        lower_value = value.lower()
        if any(token in lower_value for token in forbidden):
            raise PlanValidationError("Plan contains forbidden execution knowledge.")


def _iter_plan_strings(plan: ExecutionPlan) -> Iterable[str]:
    yield plan.intent.from_asset
    yield plan.intent.to_asset
    yield from plan.intent.notes
    yield plan.capital_state.snapshot_id
    yield from plan.capital_state.notes
    for exposure in plan.capital_state.exposures:
        yield exposure.asset_code
    for step in plan.steps:
        yield step.from_asset
        yield step.to_asset
        yield step.rationale
    yield from plan.assumptions
    yield from plan.failure_modes
    for requirement in plan.required_signatures:
        yield requirement.signer_reference
        yield requirement.purpose


def _derive_required_signatures(
    steps: Tuple[ExecutionStep, ...],
) -> Tuple[SignatureRequirement, ...]:
    requirements: List[SignatureRequirement] = []
    for step in steps:
        if step.action_type == ActionType.HOLD:
            continue
        requirements.append(
            SignatureRequirement(
                signer_reference="primary",
                purpose=f"authorize step {step.sequence}",
            )
        )
    return tuple(requirements)


def _ordered_strings(values: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(sorted(values))


def _ordered_steps(steps: Tuple[ExecutionStep, ...]) -> Tuple[ExecutionStep, ...]:
    return tuple(sorted(steps, key=lambda step: step.sequence))
