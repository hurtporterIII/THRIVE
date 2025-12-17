"""Smoke tests for the Capital OS web API."""

import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional dependency
    TestClient = None

try:
    from web import app as web_app
except ImportError:  # pragma: no cover - optional dependency
    web_app = None
from wallet_core.keystore import FileKeyStore
from wallet_core.models import DerivationPath
from wallet_core.signer import PassphraseEncryptor, WalletCore


@unittest.skipIf(TestClient is None or web_app is None, "FastAPI not available")
class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        web_app._reset_state()
        self.tempdir = tempfile.TemporaryDirectory()
        self.keystore_path = str(Path(self.tempdir.name) / "keystore.json")
        keystore = FileKeyStore(Path(self.keystore_path))
        wallet = WalletCore(
            keystore=keystore,
            encryptor=PassphraseEncryptor(),
            time_provider=lambda: "2024-01-01T00:00:00Z",
            entropy_provider=lambda n: b"\x11" * n,
        )
        metadata = wallet.create_wallet(label="Primary", passphrase="pass")
        account = wallet.add_account(metadata.wallet_id, "default", DerivationPath().to_string())
        self.wallet_id = metadata.wallet_id
        self.account_id = account.account_id
        web_app._set_active_account(self.keystore_path, self.wallet_id, self.account_id)

        self.client = TestClient(web_app.app)
        response = self.client.post(
            "/api/context",
            json={"keystore_path": self.keystore_path, "wallet_id": self.wallet_id},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_dashboard_and_status_smoke(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        payload = status.json()
        self.assertEqual(payload["wallet_state"], "LOCKED")
        self.assertNotIn("seed_phrase", payload)

    def test_status_does_not_mutate_active_account(self) -> None:
        active_before = web_app._get_active_account(self.keystore_path, self.wallet_id)
        self.client.get("/api/status")
        active_after = web_app._get_active_account(self.keystore_path, self.wallet_id)
        self.assertEqual(active_before, active_after)

    def test_unlock_and_lock(self) -> None:
        response = self.client.post("/api/wallet/unlock", json={"passphrase": "pass"})
        self.assertEqual(response.status_code, 200)
        status = self.client.get("/api/status").json()
        self.assertEqual(status["wallet_state"], "UNLOCKED")
        self.assertNotEqual(status["active_address"], "LOCKED")

        response = self.client.post("/api/wallet/lock")
        self.assertEqual(response.status_code, 200)
        status = self.client.get("/api/status").json()
        self.assertEqual(status["wallet_state"], "LOCKED")

    def test_seed_export_requires_ack_and_no_persisted_unlock(self) -> None:
        response = self.client.post(
            "/api/wallet/seed",
            json={"passphrase": "pass", "acknowledge_warning": False},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/api/wallet/seed",
            json={"passphrase": "pass", "acknowledge_warning": True},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("seed_phrase", payload)

        status = self.client.get("/api/status").json()
        self.assertEqual(status["wallet_state"], "LOCKED")

    def test_accounts_list_and_select(self) -> None:
        wallet = WalletCore(
            keystore=FileKeyStore(Path(self.keystore_path)),
            encryptor=PassphraseEncryptor(),
        )
        account_two = wallet.add_account(self.wallet_id, "secondary", "m/44'/0'/1'/0/0")

        response = self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["accounts"]), 2)

        response = self.client.post(
            "/api/accounts/select",
            json={"account_id": account_two.account_id},
        )
        self.assertEqual(response.status_code, 200)
        active_after = web_app._get_active_account(self.keystore_path, self.wallet_id)
        self.assertEqual(active_after, account_two.account_id)

    def test_plan_simulate_and_execute(self) -> None:
        plan_payload = {
            "snapshot_id": "snap-1",
            "exposures": [{"asset_code": "USD", "quantity": 1000}],
            "intent": {
                "action_type": "HOLD",
                "from_asset": "USD",
                "to_asset": "USD",
                "amount": 100,
            },
        }
        response = self.client.post("/api/plans", json=plan_payload)
        self.assertEqual(response.status_code, 200)
        plan = response.json()

        response = self.client.post("/api/simulate", json={"plan": plan})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("dry_run", payload)

        response = self.client.post(
            "/api/execution/mode",
            json={"mode": "MANUAL"},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/execution/arm", json={"armed": True})
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/api/execute",
            json={"plan": plan, "confirm_all": True},
        )
        self.assertEqual(response.status_code, 200)
        exec_payload = response.json()
        self.assertEqual(exec_payload["mode"], "MANUAL")
        self.assertTrue(exec_payload["decisions"])


if __name__ == "__main__":
    unittest.main()
