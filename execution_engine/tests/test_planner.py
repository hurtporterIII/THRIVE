"""Determinism and explainability tests for the execution planner."""

import inspect
import unittest
from dataclasses import replace

from execution_engine.models import (
    ActionType,
    CapitalExposure,
    CapitalState,
    ExecutionIntent,
    ExecutionPlan,
    ExecutionStep,
    SignatureRequirement,
)
from execution_engine.planner import (
    ExecutionPlanner,
    PlanValidationError,
    UnimplementedIntentError,
    validate_plan,
)


class ExecutionPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = ExecutionPlanner()
        self.capital_state = CapitalState(
            snapshot_id="snap-001",
            exposures=(CapitalExposure(asset_code="BTC", quantity=2.0),),
        )

    def _build_swap_intent(self) -> ExecutionIntent:
        return ExecutionIntent(
            action_type=ActionType.SWAP,
            from_asset="BTC",
            to_asset="USD",
            amount=1.5,
        )

    def _build_plan(self) -> ExecutionPlan:
        return self.planner.plan(self._build_swap_intent(), self.capital_state)

    def test_same_input_same_plan(self) -> None:
        intent = self._build_swap_intent()
        first = self.planner.plan(intent, self.capital_state)
        second = self.planner.plan(intent, self.capital_state)

        self.assertEqual(first, second)
        self.assertEqual(first.steps[0].rationale, "SWAP 1.5 BTC to USD.")

    def test_plan_has_assumptions_and_failure_modes(self) -> None:
        plan = self._build_plan()

        self.assertTrue(plan.assumptions)
        self.assertTrue(plan.failure_modes)
        self.assertTrue(plan.required_signatures)
        self.assertEqual(plan.capital_state.snapshot_id, "snap-001")

    def test_unimplemented_intent_fails(self) -> None:
        intent = ExecutionIntent(
            action_type="UNKNOWN",
            from_asset="BTC",
            to_asset="USD",
            amount=1.0,
        )
        with self.assertRaises(UnimplementedIntentError):
            self.planner.plan(intent, self.capital_state)

    def test_plan_completeness_enforced(self) -> None:
        plan = self._build_plan()

        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=()))
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, assumptions=()))
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, failure_modes=()))
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, estimated_cost=None))

    def test_step_validity_enforced(self) -> None:
        plan = self._build_plan()
        invalid_step = ExecutionStep(
            sequence=1,
            action_type=ActionType.SWAP,
            from_asset="BTC",
            to_asset="BTC",
            amount=1.0,
            rationale="Swap BTC to BTC.",
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=(invalid_step,)))

        invalid_amount = ExecutionStep(
            sequence=1,
            action_type=ActionType.TRANSFER,
            from_asset="BTC",
            to_asset="USD",
            amount=0.0,
            rationale="Transfer with zero amount.",
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=(invalid_amount,)))

        invalid_rationale = ExecutionStep(
            sequence=1,
            action_type=ActionType.TRANSFER,
            from_asset="BTC",
            to_asset="USD",
            amount=1.0,
            rationale="",
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=(invalid_rationale,)))

        invalid_action_type = ExecutionStep(
            sequence=1,
            action_type="SWAP",
            from_asset="BTC",
            to_asset="USD",
            amount=1.0,
            rationale="Invalid action type.",
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=(invalid_action_type,)))

    def test_capital_conservation_enforced(self) -> None:
        plan = self._build_plan()
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, estimated_cost=1.0))

    def test_deterministic_ordering_enforced(self) -> None:
        plan = self._build_plan()
        step_one = plan.steps[0]
        step_two = replace(step_one, sequence=2)
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, steps=(step_two, step_one)))

        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, assumptions=("b", "a")))

        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, failure_modes=("b", "a")))

    def test_signature_consistency_enforced(self) -> None:
        plan = self._build_plan()
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, required_signatures=()))

        hold_intent = ExecutionIntent(
            action_type=ActionType.HOLD,
            from_asset="BTC",
            to_asset="BTC",
            amount=0.0,
        )
        hold_plan = self.planner.plan(hold_intent, self.capital_state)
        with self.assertRaises(PlanValidationError):
            validate_plan(
                replace(
                    hold_plan,
                    required_signatures=(
                        SignatureRequirement(
                            signer_reference="primary",
                            purpose="unexpected signature",
                        ),
                    ),
                )
            )

    def test_no_hidden_execution_knowledge(self) -> None:
        plan = self._build_plan()
        with self.assertRaises(PlanValidationError):
            validate_plan(replace(plan, assumptions=("gas fee applied",)))

    def test_planner_has_no_wallet_dependency(self) -> None:
        source = inspect.getsource(ExecutionPlanner)
        self.assertNotIn("wallet_core", source)


if __name__ == "__main__":
    unittest.main()
