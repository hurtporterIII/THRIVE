"""Smoke tests for the operator CLI."""

import json
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from io import StringIO

from operator_cli.cli import main


class OperatorCliSmokeTests(unittest.TestCase):
    def _run(self, args):
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(args)
        return code, buf.getvalue()

    def test_plan_create_outputs_json(self) -> None:
        code, output = self._run(
            [
                "plan",
                "create",
                "--action",
                "SWAP",
                "--from-asset",
                "ETH",
                "--to-asset",
                "USDC",
                "--amount",
                "1.0",
                "--snapshot-id",
                "snap-cli-001",
                "--exposure",
                "ETH=2.0",
                "--exposure",
                "USDC=1000.0",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["intent"]["action_type"], "SWAP")

    def test_execute_manual_yes(self) -> None:
        plan_code, plan_output = self._run(
            [
                "plan",
                "create",
                "--action",
                "SWAP",
                "--from-asset",
                "ETH",
                "--to-asset",
                "USDC",
                "--amount",
                "1.0",
                "--snapshot-id",
                "snap-cli-002",
                "--exposure",
                "ETH=2.0",
                "--exposure",
                "USDC=1000.0",
            ]
        )
        self.assertEqual(plan_code, 0)
        buf = StringIO(plan_output)
        with _redirect_stdin(buf):
            code, output = self._run(
                [
                    "execute",
                    "--plan",
                    "-",
                    "--mode",
                    "manual",
                    "--arm",
                    "--yes",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["mode"], "manual")
        self.assertTrue(payload["decisions"])


if __name__ == "__main__":
    unittest.main()


@contextmanager
def _redirect_stdin(stream):
    original = sys.stdin
    try:
        sys.stdin = stream
        yield
    finally:
        sys.stdin = original
