"""Translate execution plans into unsigned Ethereum transaction payloads."""

from typing import Dict, Tuple

from execution_engine.models import ActionType, ExecutionPlan, ExecutionStep
from execution_engine.planner import validate_plan

from .models import EthereumTxPayload


class AdapterError(ValueError):
    """Raised when a plan cannot be adapted to Ethereum payloads."""


_ACTION_TO_ADDRESS: Dict[ActionType, str] = {
    ActionType.TRANSFER: "0x0000000000000000000000000000000000000001",
    ActionType.SWAP: "0x0000000000000000000000000000000000000002",
}


def plan_to_payloads(plan: ExecutionPlan) -> Tuple[EthereumTxPayload, ...]:
    validate_plan(plan)

    payloads = []
    for step in plan.steps:
        if step.action_type == ActionType.HOLD:
            continue
        if step.action_type not in _ACTION_TO_ADDRESS:
            raise AdapterError("Unsupported action type for Ethereum adapter.")
        payloads.append(_step_to_payload(step))

    return tuple(payloads)


def _step_to_payload(step: ExecutionStep) -> EthereumTxPayload:
    if not step.from_asset or not step.to_asset:
        raise AdapterError("Step assets must be non-empty for Ethereum payloads.")

    to_address = _ACTION_TO_ADDRESS[step.action_type]
    payload_data = _encode_step_data(step)

    return EthereumTxPayload(
        step_sequence=step.sequence,
        to_address=to_address,
        data=payload_data,
        value_wei=0,
    )


def _encode_step_data(step: ExecutionStep) -> str:
    amount_str = _format_amount(step.amount)
    payload = (
        f"{step.action_type.value}|"
        f"{step.from_asset}|"
        f"{step.to_asset}|"
        f"{amount_str}|"
        f"{step.sequence}"
    )
    return _to_hex(payload.encode("ascii"))


def _format_amount(value: float) -> str:
    return f"{value:.18g}"


def _to_hex(data: bytes) -> str:
    return "0x" + data.hex()
