"""Execution controller modes and decisions."""

from dataclasses import dataclass
from enum import Enum


class ExecutionMode(Enum):
    SAFE = "SAFE"
    MANUAL = "MANUAL"
    GUARDED = "GUARDED"


@dataclass(frozen=True)
class ExecutionDecision:
    step_sequence: int
    allowed: bool
    reason: str
