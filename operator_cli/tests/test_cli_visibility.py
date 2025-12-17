"""Visibility tests for status and prove commands."""

import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os

from operator_cli import cli


class OperatorCliVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keystore = "mem://visibility"
        cli._MEMORY_KEYSTORES.pop(self.keystore, None)
        cli._ACTIVE_ACCOUNTS.pop(self.keystore, None)

    def _run(self, args):
        out_buf = StringIO()
        err_buf = StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = cli.main(args)
        return code, out_buf.getvalue(), err_buf.getvalue()

    def _init_wallet(self):
        code, output, _ = self._run(
            [
                "wallet",
                "init",
                "--keystore",
                self.keystore,
                "--label",
                "primary",
                "--passphrase",
                "pass",
            ]
        )
        self.assertEqual(code, 0)
        return output.strip()

    def test_status_runs_without_error(self) -> None:
        wallet_id = self._init_wallet()
        code, output, _ = self._run(
            [
                "status",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--passphrase",
                "pass",
                "--snapshot-id",
                "snap-001",
                "--exposure",
                "ETH=2.0",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["wallet_state"], "UNLOCKED")
        self.assertEqual(payload["active_account"], "default")
        self.assertNotEqual(payload["active_address"], "LOCKED")
        self.assertEqual(
            set(payload.keys()),
            {
                "wallet_state",
                "active_account",
                "active_address",
                "capital_total",
                "exposures",
                "execution_mode",
                "last_plan",
                "last_dry_run",
            },
        )

    def test_prove_fails_when_locked(self) -> None:
        wallet_id = self._init_wallet()
        code, _, err = self._run(
            [
                "prove",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Wallet is locked", err)

    def test_prove_succeeds_when_unlocked(self) -> None:
        wallet_id = self._init_wallet()
        code, output, _ = self._run(
            [
                "prove",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--passphrase",
                "pass",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["verification"], "PASS")
        self.assertEqual(payload["account"], "default")

    def test_visibility_does_not_mutate_state(self) -> None:
        wallet_id = self._init_wallet()
        store_before = copy.deepcopy(cli._MEMORY_KEYSTORES[self.keystore])
        active_before = copy.deepcopy(cli._ACTIVE_ACCOUNTS[self.keystore])

        self._run(
            [
                "status",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--snapshot-id",
                "snap-002",
                "--exposure",
                "ETH=2.0",
            ]
        )
        self._run(
            [
                "prove",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--passphrase",
                "pass",
            ]
        )
        store_after = cli._MEMORY_KEYSTORES[self.keystore]
        active_after = cli._ACTIVE_ACCOUNTS[self.keystore]

        self.assertEqual(store_before.keys(), store_after.keys())
        for key in store_before:
            self.assertEqual(
                store_before[key].to_dict(),
                store_after[key].to_dict(),
            )
        self.assertEqual(active_before, active_after)

    def test_active_account_set_on_init(self) -> None:
        wallet_id = self._init_wallet()
        self.assertIn(wallet_id, cli._ACTIVE_ACCOUNTS[self.keystore])

    def test_status_without_color_support(self) -> None:
        wallet_id = self._init_wallet()
        os.environ["NO_COLOR"] = "1"
        try:
            code, output, _ = self._run(
                [
                    "status",
                    "--keystore",
                    self.keystore,
                    "--wallet-id",
                    wallet_id,
                ]
            )
        finally:
            os.environ.pop("NO_COLOR", None)
        self.assertEqual(code, 0)
        self.assertIn("Capital OS Status", output)


if __name__ == "__main__":
    unittest.main()
