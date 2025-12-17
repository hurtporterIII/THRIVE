"""Controlled execution controller for manual and guarded modes."""

from typing import Callable, Optional, Tuple

from execution_engine.models import ExecutionPlan, ExecutionStep
from execution_engine.planner import validate_plan

from .modes import ExecutionDecision, ExecutionMode
from .policy import GuardPolicy


class ExecutionBlockedError(RuntimeError):
    """Raised when execution is blocked by mode or arming state."""


class ModeTransitionError(ValueError):
    """Raised when an invalid mode transition is attempted."""


class PolicyViolationError(RuntimeError):
    """Raised when a guarded policy violation is detected."""


class ExecutionController:
    """Controls plan execution gating without performing execution."""

    def __init__(self) -> None:
        self._mode = ExecutionMode.SAFE
        self._armed = False
        self._policy: Optional[GuardPolicy] = None

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @property
    def armed(self) -> bool:
        return self._armed

    def arm(self) -> None:
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    def set_mode(self, mode: ExecutionMode, policy: Optional[GuardPolicy] = None) -> None:
        if mode != ExecutionMode.SAFE and self._mode != ExecutionMode.SAFE:
            raise ModeTransitionError("Mode escalation must pass through SAFE.")

        if mode == ExecutionMode.GUARDED and policy is None:
            raise PolicyViolationError("Guarded mode requires an explicit policy.")

        if mode != ExecutionMode.GUARDED and policy is not None:
            raise ModeTransitionError("Policies may only be set in GUARDED mode.")

        self._mode = mode
        self._policy = policy if mode == ExecutionMode.GUARDED else None

    def evaluate_plan(
        self,
        plan: ExecutionPlan,
        confirm_step: Optional[Callable[[ExecutionStep], bool]] = None,
    ) -> Tuple[ExecutionDecision, ...]:
        validate_plan(plan)

        if not self._armed:
            raise ExecutionBlockedError("Execution is not armed.")

        if self._mode == ExecutionMode.SAFE:
            raise ExecutionBlockedError("SAFE mode blocks execution.")

        if self._mode == ExecutionMode.MANUAL:
            return self._evaluate_manual(plan, confirm_step)

        if self._mode == ExecutionMode.GUARDED:
            return self._evaluate_guarded(plan)

        raise ExecutionBlockedError("Unknown execution mode.")

    def _evaluate_manual(
        self,
        plan: ExecutionPlan,
        confirm_step: Optional[Callable[[ExecutionStep], bool]],
    ) -> Tuple[ExecutionDecision, ...]:
        if confirm_step is None:
            raise ExecutionBlockedError("Manual mode requires per-step confirmation.")

        decisions = []
        for step in plan.steps:
            allowed = bool(confirm_step(step))
            if not allowed:
                raise ExecutionBlockedError("Manual confirmation rejected.")
            decisions.append(
                ExecutionDecision(
                    step_sequence=step.sequence,
                    allowed=True,
                    reason="Confirmed manually.",
                )
            )
        return tuple(decisions)

    def _evaluate_guarded(self, plan: ExecutionPlan) -> Tuple[ExecutionDecision, ...]:
        if self._policy is None:
            raise PolicyViolationError("Guarded mode requires a policy.")

        violations = self._policy.validate_steps(plan.steps)
        if violations:
            raise PolicyViolationError("Policy violation: " + ", ".join(violations))

        decisions = []
        for step in plan.steps:
            decisions.append(
                ExecutionDecision(
                    step_sequence=step.sequence,
                    allowed=True,
                    reason="Guarded policy passed.",
                )
            )
        return tuple(decisions)
