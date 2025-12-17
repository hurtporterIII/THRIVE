"""Simulate Ethereum payload execution without network calls."""

from typing import Iterable, Tuple

from .models import DryRunResult, DryRunTxResult, EthereumTxPayload


class SimulationError(ValueError):
    """Raised when a dry-run simulation cannot be performed."""


_DEFAULT_GAS_USED = 21_000
_DEFAULT_GAS_PRICE_WEI = 1


def simulate(payloads: Iterable[EthereumTxPayload]) -> DryRunResult:
    tx_results = []
    total_gas = 0
    total_cost = 0

    for payload in payloads:
        _validate_payload(payload)
        gas_used = _DEFAULT_GAS_USED
        cost = gas_used * _DEFAULT_GAS_PRICE_WEI
        tx_results.append(
            DryRunTxResult(
                step_sequence=payload.step_sequence,
                success=True,
                gas_used=gas_used,
                cost_wei=cost,
                notes=("Dry-run only; no execution performed.",),
            )
        )
        total_gas += gas_used
        total_cost += cost

    return DryRunResult(
        success=True,
        tx_results=tuple(tx_results),
        total_gas_used=total_gas,
        total_cost_wei=total_cost,
        notes=("Simulation completed without network calls.",),
    )


def _validate_payload(payload: EthereumTxPayload) -> None:
    if not payload.to_address:
        raise SimulationError("Payload must include a target address.")
    if not payload.data.startswith("0x"):
        raise SimulationError("Payload data must be hex-prefixed.")
    if payload.value_wei < 0:
        raise SimulationError("Payload value must be non-negative.")
