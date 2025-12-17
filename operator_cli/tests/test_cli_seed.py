"""Seed export tests for the operator CLI."""

import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from operator_cli import cli


class OperatorCliSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keystore = "mem://seed"
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

    def test_seed_fails_when_locked(self) -> None:
        wallet_id = self._init_wallet()
        code, _, err = self._run(
            [
                "wallet",
                "seed",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--passphrase",
                "",
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Wallet is locked", err)

    def test_seed_succeeds_when_unlocked(self) -> None:
        wallet_id = self._init_wallet()
        code, output, _ = self._run(
            [
                "wallet",
                "seed",
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
        phrase = payload["seed_phrase"]
        self.assertTrue(phrase)
        record_json = json.dumps(cli._MEMORY_KEYSTORES[self.keystore][wallet_id].to_dict())
        self.assertNotIn(phrase, record_json)

    def test_clipboard_copy_optional(self) -> None:
        wallet_id = self._init_wallet()
        original = cli._copy_to_clipboard
        cli._copy_to_clipboard = lambda value: False
        try:
            code, output, _ = self._run(
                [
                    "wallet",
                    "seed",
                    "--keystore",
                    self.keystore,
                    "--wallet-id",
                    wallet_id,
                    "--passphrase",
                    "pass",
                ]
            )
        finally:
            cli._copy_to_clipboard = original
        self.assertEqual(code, 0)
        self.assertIn("[ready to copy]", output)

    def test_seed_view_does_not_mutate_state(self) -> None:
        wallet_id = self._init_wallet()
        store_before = copy.deepcopy(cli._MEMORY_KEYSTORES[self.keystore])
        active_before = copy.deepcopy(cli._ACTIVE_ACCOUNTS[self.keystore])

        self._run(
            [
                "wallet",
                "seed",
                "--keystore",
                self.keystore,
                "--wallet-id",
                wallet_id,
                "--passphrase",
                "pass",
                "--json",
            ]
        )

        store_after = cli._MEMORY_KEYSTORES[self.keystore]
        active_after = cli._ACTIVE_ACCOUNTS[self.keystore]

        for key in store_before:
            self.assertEqual(
                store_before[key].to_dict(),
                store_after[key].to_dict(),
            )
        self.assertEqual(active_before, active_after)


if __name__ == "__main__":
    unittest.main()
