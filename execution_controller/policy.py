"""Guarded execution policy."""

from dataclasses import dataclass
from typing import Iterable, Tuple

from execution_engine.models import ActionType, ExecutionStep


@dataclass(frozen=True)
class GuardPolicy:
    allowed_action_types: Tuple[ActionType, ...]
    allowed_assets: Tuple[str, ...] | None = None

    def validate_step(self, step: ExecutionStep) -> Tuple[str, ...]:
        violations = []

        if step.action_type not in self.allowed_action_types:
            violations.append("Action type not allowed.")

        if self.allowed_assets is not None:
            if step.from_asset not in self.allowed_assets:
                violations.append("from_asset not allowed.")
            if step.to_asset not in self.allowed_assets:
                violations.append("to_asset not allowed.")

        return tuple(violations)

    def validate_steps(self, steps: Iterable[ExecutionStep]) -> Tuple[str, ...]:
        violations = []
        for step in steps:
            violations.extend(self.validate_step(step))
        return tuple(violations)
