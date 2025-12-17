"""Adapter integrity tests for core usage."""

import json
import unittest
import html

from core.engine import calculate_true_wealth
from core.models import PositionInput
from thrive import truth_engine

try:
    from web import app as web_app
    from fastapi.responses import HTMLResponse
except ImportError:  # pragma: no cover - optional adapter dependency
    web_app = None
    HTMLResponse = None


class AdapterIntegrityTests(unittest.TestCase):
    def test_thrive_adapter_matches_core_output(self) -> None:
        position = PositionInput(
            asset_type="stock",
            ticker="ADAPT",
            quantity=12.0,
            cost_basis_per_unit=20.0,
            current_price=25.0,
            days_held=500,
            state_tax_rate=0.03,
        )

        core_result = calculate_true_wealth(position).to_dict()
        wrapper_result = truth_engine.calculate_true_wealth(position)

        self.assertIsInstance(wrapper_result, dict)
        self.assertEqual(core_result, wrapper_result)
        self.assertIsInstance(wrapper_result["assumptions"], list)

    @unittest.skipIf(web_app is None, "FastAPI adapter not available")
    def test_web_adapter_does_not_mutate_output(self) -> None:
        position = PositionInput(
            asset_type="crypto",
            ticker="WEB",
            quantity=2.0,
            cost_basis_per_unit=100.0,
            current_price=150.0,
            days_held=200,
            state_tax_rate=0.0,
        )

        core_result = calculate_true_wealth(position).to_dict()
        expected_json = html.escape(json.dumps(core_result, indent=2))

        response = _run_async(
            web_app.calculate(
                asset_type=position.asset_type,
                quantity=position.quantity,
                cost_basis_per_unit=position.cost_basis_per_unit,
                current_price=position.current_price,
                days_held=position.days_held,
                state_tax_rate=str(position.state_tax_rate),
            )
        )

        self.assertIsInstance(response, HTMLResponse)
        body = response.body.decode("utf-8")
        self.assertIn(expected_json, body)


def _run_async(awaitable):
    try:
        import asyncio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Asyncio is required for adapter tests.") from exc

    if asyncio.iscoroutine(awaitable):
        return asyncio.run(awaitable)
    return awaitable


if __name__ == "__main__":
    unittest.main()
