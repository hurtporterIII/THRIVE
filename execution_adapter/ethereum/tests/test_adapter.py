"""Determinism, safety, and explainability tests for the Ethereum adapter."""

import inspect
import unittest

from execution_engine.models import ActionType, CapitalExposure, CapitalState, ExecutionIntent
from execution_engine.planner import ExecutionPlanner

from execution_adapter.ethereum.adapter import AdapterError, plan_to_payloads
from execution_adapter.ethereum.simulator import SimulationError, simulate


class EthereumAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = ExecutionPlanner()
        self.capital_state = CapitalState(
            snapshot_id="snap-eth-001",
            exposures=(
                CapitalExposure(asset_code="ETH", quantity=3.0),
                CapitalExposure(asset_code="USDC", quantity=1000.0),
            ),
        )

    def test_deterministic_payloads(self) -> None:
        intent = ExecutionIntent(
            action_type=ActionType.TRANSFER,
            from_asset="ETH",
            to_asset="USDC",
            amount=1.0,
        )
        plan = self.planner.plan(intent, self.capital_state)

        first = plan_to_payloads(plan)
        second = plan_to_payloads(plan)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].data.startswith("0x"))

    def test_one_payload_per_step(self) -> None:
        intent = ExecutionIntent(
            action_type=ActionType.SWAP,
            from_asset="ETH",
            to_asset="USDC",
            amount=0.5,
        )
        plan = self.planner.plan(intent, self.capital_state)
        payloads = plan_to_payloads(plan)
        self.assertEqual(len(payloads), len(plan.steps))

        hold_intent = ExecutionIntent(
            action_type=ActionType.HOLD,
            from_asset="ETH",
            to_asset="ETH",
            amount=0.0,
        )
        hold_plan = self.planner.plan(hold_intent, self.capital_state)
        self.assertEqual(plan_to_payloads(hold_plan), ())

    def test_unsupported_step_fails(self) -> None:
        intent = ExecutionIntent(
            action_type=ActionType.SWAP,
            from_asset="",
            to_asset="USDC",
            amount=1.0,
        )
        plan = self.planner.plan(intent, self.capital_state)
        with self.assertRaises(AdapterError):
            plan_to_payloads(plan)

    def test_simulator_explainable(self) -> None:
        intent = ExecutionIntent(
            action_type=ActionType.SWAP,
            from_asset="ETH",
            to_asset="USDC",
            amount=0.25,
        )
        plan = self.planner.plan(intent, self.capital_state)
        payloads = plan_to_payloads(plan)

        result = simulate(payloads)

        self.assertTrue(result.success)
        self.assertTrue(result.tx_results)
        self.assertGreater(result.total_gas_used, 0)
        self.assertGreater(result.total_cost_wei, 0)
        self.assertTrue(result.notes)

    def test_simulator_rejects_invalid_payload(self) -> None:
        intent = ExecutionIntent(
            action_type=ActionType.TRANSFER,
            from_asset="ETH",
            to_asset="USDC",
            amount=1.0,
        )
        plan = self.planner.plan(intent, self.capital_state)
        payloads = list(plan_to_payloads(plan))
        bad_payload = payloads[0].__class__(
            step_sequence=payloads[0].step_sequence,
            to_address="",
            data=payloads[0].data,
            value_wei=payloads[0].value_wei,
        )
        with self.assertRaises(SimulationError):
            simulate([bad_payload])

    def test_adapter_has_no_wallet_dependency(self) -> None:
        source = inspect.getsource(plan_to_payloads)
        self.assertNotIn("wallet_core", source)


if __name__ == "__main__":
    unittest.main()
