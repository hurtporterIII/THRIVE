"""API contract and safety tests for the web adapter."""

import contextlib
import tempfile
from pathlib import Path

import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional dependency
    TestClient = None

try:
    from web.app import app
except ImportError:  # pragma: no cover - optional dependency
    app = None
try:
    from web import app as web_app
except ImportError:  # pragma: no cover - optional dependency
    web_app = None

from wallet_core.keystore import FileKeyStore
from wallet_core.models import DerivationPath
from wallet_core.signer import PassphraseEncryptor, WalletCore


@contextlib.contextmanager
def _client_with_context():
    if TestClient is None or app is None:
        raise RuntimeError("FastAPI not available")
    client = TestClient(app)
    with tempfile.TemporaryDirectory() as tempdir:
        keystore_path = Path(tempdir) / "keystore.json"
        keystore = FileKeyStore(keystore_path)
        wallet = WalletCore(
            keystore=keystore,
            encryptor=PassphraseEncryptor(),
            time_provider=lambda: "2024-01-01T00:00:00Z",
            entropy_provider=lambda n: b"\x01" * n,
        )
        metadata = wallet.create_wallet(label="Primary", passphrase="pass")
        wallet.add_account(metadata.wallet_id, "default", DerivationPath().to_string())
        response = client.post(
            "/api/context",
            json={"keystore_path": str(keystore_path), "wallet_id": metadata.wallet_id},
        )
        if response.status_code != 200:
            raise RuntimeError("Failed to set context")
        yield client, metadata.wallet_id


@unittest.skipIf(TestClient is None or app is None, "FastAPI not available")
class WebBreakItTests(unittest.TestCase):
    def setUp(self) -> None:
        if web_app is not None:
            web_app._reset_state()

    def test_app_boots(self) -> None:
        client = TestClient(app)
        response = client.get("/")
        self.assertIn(response.status_code, (200, 302))

    def test_no_context_protection(self) -> None:
        client = TestClient(app)
        response = client.post("/api/wallet/unlock", json={"passphrase": "x"})
        self.assertEqual(response.status_code, 400)

    def test_seed_export_requires_consent(self) -> None:
        with _client_with_context() as (client, _wallet_id):
            response = client.post(
                "/api/wallet/seed",
                json={"passphrase": "pass", "acknowledge_warning": False},
            )
            self.assertIn(response.status_code, (400, 403))

    def test_ai_without_key(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/advisor",
            json={"enabled": False, "api_key": "", "plan": None, "simulation": None, "snapshot": None},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("disabled", response.text.lower())

    def test_ai_secret_stripping(self) -> None:
        from web.ai_adapter import sanitize_context

        ctx = {
            "seed": "secret",
            "passphrase": "secret",
            "private_key": "secret",
            "plan": {"action": "HOLD"},
        }

        sanitized = sanitize_context(ctx)

        self.assertNotIn("seed", sanitized)
        self.assertNotIn("passphrase", sanitized)
        self.assertNotIn("private_key", sanitized)

    def test_execute_requires_arm(self) -> None:
        client = TestClient(app)
        plan_response = client.post(
            "/api/plans",
            json={
                "snapshot_id": "snap-1",
                "exposures": [{"asset_code": "USD", "quantity": 1000}],
                "intent": {
                    "action_type": "HOLD",
                    "from_asset": "USD",
                    "to_asset": "USD",
                    "amount": 0,
                },
            },
        )
        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.json()

        response = client.post("/api/execute", json={"plan": plan, "confirm_all": True})
        self.assertIn(response.status_code, (400, 403))

    def test_json_contracts(self) -> None:
        with _client_with_context() as (client, _wallet_id):
            response = client.get("/api/status?json=1")
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response.json(), dict)

    def test_double_execute_blocked(self) -> None:
        client = TestClient(app)
        plan_response = client.post(
            "/api/plans",
            json={
                "snapshot_id": "snap-1",
                "exposures": [{"asset_code": "USD", "quantity": 1000}],
                "intent": {
                    "action_type": "HOLD",
                    "from_asset": "USD",
                    "to_asset": "USD",
                    "amount": 0,
                },
            },
        )
        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.json()

        r1 = client.post("/api/execute", json={"plan": plan, "confirm_all": True})
        r2 = client.post("/api/execute", json={"plan": plan, "confirm_all": True})

        self.assertTrue(r1.status_code != 200 or r2.status_code != 200)

    def test_invalid_action_type(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/plans",
            json={
                "snapshot_id": "snap-1",
                "exposures": [{"asset_code": "USD", "quantity": 1000}],
                "intent": {
                    "action_type": "NUKE",
                    "from_asset": "USD",
                    "to_asset": "EUR",
                    "amount": 100,
                },
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_negative_amount(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/plans",
            json={
                "snapshot_id": "snap-1",
                "exposures": [{"asset_code": "USD", "quantity": 1000}],
                "intent": {
                    "action_type": "TRANSFER",
                    "from_asset": "USD",
                    "to_asset": "EUR",
                    "amount": -100,
                },
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_execute_without_simulation(self) -> None:
        client = TestClient(app)
        plan_response = client.post(
            "/api/plans",
            json={
                "snapshot_id": "snap-1",
                "exposures": [{"asset_code": "USD", "quantity": 1000}],
                "intent": {
                    "action_type": "HOLD",
                    "from_asset": "USD",
                    "to_asset": "USD",
                    "amount": 0,
                },
            },
        )
        self.assertEqual(plan_response.status_code, 200)
        plan = plan_response.json()

        response = client.post("/api/execute", json={"plan": plan, "confirm_all": True})
        self.assertIn(response.status_code, (400, 403))


if __name__ == "__main__":
    unittest.main()
