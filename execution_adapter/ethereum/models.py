"""Ethereum adapter models for unsigned payloads and dry-run output."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class EthereumTxPayload:
    step_sequence: int
    to_address: str
    data: str
    value_wei: int


@dataclass(frozen=True)
class DryRunTxResult:
    step_sequence: int
    success: bool
    gas_used: int
    cost_wei: int
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DryRunResult:
    success: bool
    tx_results: Tuple[DryRunTxResult, ...]
    total_gas_used: int
    total_cost_wei: int
    notes: Tuple[str, ...] = ()
