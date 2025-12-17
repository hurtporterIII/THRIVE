"""Unit tests for execution controller modes and switching."""

import unittest

from execution_engine.models import ActionType, CapitalExposure, CapitalState, ExecutionIntent
from execution_engine.planner import ExecutionPlanner

from execution_controller.controller import (
    ExecutionBlockedError,
    ExecutionController,
    ModeTransitionError,
    PolicyViolationError,
)
from execution_controller.modes import ExecutionMode
from execution_controller.policy import GuardPolicy


class ExecutionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ExecutionController()
        self.planner = ExecutionPlanner()
        self.capital_state = CapitalState(
            snapshot_id="snap-ctrl-001",
            exposures=(CapitalExposure(asset_code="ETH", quantity=2.0),),
        )
        self.intent = ExecutionIntent(
            action_type=ActionType.SWAP,
            from_asset="ETH",
            to_asset="USDC",
            amount=1.0,
        )

    def _build_plan(self):
        return self.planner.plan(self.intent, self.capital_state)

    def test_safe_mode_blocks_execution(self) -> None:
        plan = self._build_plan()
        self.controller.arm()
        with self.assertRaises(ExecutionBlockedError):
            self.controller.evaluate_plan(plan, confirm_step=lambda _: True)

    def test_unarmed_blocks_execution(self) -> None:
        plan = self._build_plan()
        self.controller.set_mode(ExecutionMode.MANUAL)
        with self.assertRaises(ExecutionBlockedError):
            self.controller.evaluate_plan(plan, confirm_step=lambda _: True)

    def test_manual_mode_requires_confirmation(self) -> None:
        plan = self._build_plan()
        self.controller.set_mode(ExecutionMode.MANUAL)
        self.controller.arm()

        with self.assertRaises(ExecutionBlockedError):
            self.controller.evaluate_plan(plan)

        decisions = self.controller.evaluate_plan(plan, confirm_step=lambda _: True)
        self.assertEqual(len(decisions), len(plan.steps))

    def test_manual_mode_rejects_step(self) -> None:
        plan = self._build_plan()
        self.controller.set_mode(ExecutionMode.MANUAL)
        self.controller.arm()

        with self.assertRaises(ExecutionBlockedError):
            self.controller.evaluate_plan(plan, confirm_step=lambda _: False)

    def test_guarded_mode_requires_policy(self) -> None:
        with self.assertRaises(PolicyViolationError):
            self.controller.set_mode(ExecutionMode.GUARDED)

    def test_guarded_mode_policy_violation(self) -> None:
        plan = self._build_plan()
        policy = GuardPolicy(
            allowed_action_types=(ActionType.SWAP,),
            allowed_assets=("ETH",),
        )
        self.controller.set_mode(ExecutionMode.GUARDED, policy=policy)
        self.controller.arm()

        with self.assertRaises(PolicyViolationError):
            self.controller.evaluate_plan(plan)

    def test_guarded_mode_policy_pass(self) -> None:
        plan = self._build_plan()
        policy = GuardPolicy(
            allowed_action_types=(ActionType.SWAP,),
            allowed_assets=("ETH", "USDC"),
        )
        self.controller.set_mode(ExecutionMode.GUARDED, policy=policy)
        self.controller.arm()

        decisions = self.controller.evaluate_plan(plan)
        self.assertEqual(len(decisions), len(plan.steps))

    def test_mode_transition_requires_safe(self) -> None:
        self.controller.set_mode(ExecutionMode.MANUAL)
        policy = GuardPolicy(allowed_action_types=(ActionType.SWAP,))
        with self.assertRaises(ModeTransitionError):
            self.controller.set_mode(ExecutionMode.GUARDED, policy=policy)

    def test_policy_only_in_guarded_mode(self) -> None:
        policy = GuardPolicy(allowed_action_types=(ActionType.SWAP,))
        with self.assertRaises(ModeTransitionError):
            self.controller.set_mode(ExecutionMode.SAFE, policy=policy)


if __name__ == "__main__":
    unittest.main()
