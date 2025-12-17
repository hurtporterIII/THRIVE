"""Domain models for the execution planning engine."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class ActionType(Enum):
    HOLD = "HOLD"
    SWAP = "SWAP"
    TRANSFER = "TRANSFER"


@dataclass(frozen=True)
class CapitalExposure:
    asset_code: str
    quantity: float


@dataclass(frozen=True)
class CapitalState:
    snapshot_id: str
    exposures: Tuple[CapitalExposure, ...]
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionIntent:
    action_type: ActionType
    from_asset: str
    to_asset: str
    amount: float
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SignatureRequirement:
    signer_reference: str
    purpose: str


@dataclass(frozen=True)
class ExecutionStep:
    sequence: int
    action_type: ActionType
    from_asset: str
    to_asset: str
    amount: float
    rationale: str


@dataclass(frozen=True)
class ExecutionPlan:
    intent: ExecutionIntent
    capital_state: CapitalState
    steps: Tuple[ExecutionStep, ...]
    assumptions: Tuple[str, ...]
    failure_modes: Tuple[str, ...]
    required_signatures: Tuple[SignatureRequirement, ...]
    estimated_cost: float
