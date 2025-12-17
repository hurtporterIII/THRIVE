"""Unit tests for wallet core safety and signing."""

import unittest
import base64
import json

from wallet_core.models import DerivationPath
from wallet_core.signer import PassphraseEncryptor, WalletCore


class WalletCoreTests(unittest.TestCase):
    class InMemoryKeyStore:
        def __init__(self) -> None:
            self._records = {}

        def store(self, record) -> None:
            self._records[record.metadata.wallet_id] = record

        def load(self, wallet_id: str):
            if wallet_id not in self._records:
                raise KeyError(f"Unknown wallet_id: {wallet_id}")
            return self._records[wallet_id]

        def list_metadata(self):
            return tuple(record.metadata for record in self._records.values())

    def _make_wallet(self, seed: bytes):
        keystore = self.InMemoryKeyStore()
        encryptor = PassphraseEncryptor(
            salt_provider=lambda n: b"s" * n,
            nonce_provider=lambda n: b"n" * n,
        )
        wallet = WalletCore(
            keystore=keystore,
            encryptor=encryptor,
            time_provider=lambda: "2024-01-01T00:00:00Z",
            entropy_provider=lambda n: seed,
        )
        metadata = wallet.create_wallet(label="Primary", passphrase="pass")
        return wallet, keystore, metadata

    def test_private_seed_not_written_plaintext(self) -> None:
        seed = b"\x01" * 32
        wallet, keystore, metadata = self._make_wallet(seed)
        record = keystore.load(metadata.wallet_id)
        record_json = json.dumps(record.to_dict())
        ciphertext = base64.b64decode(record.encrypted_seed.ciphertext)

        self.assertNotEqual(ciphertext, seed)
        self.assertNotIn(seed.hex(), record_json)

    def test_lock_unlock_and_signing(self) -> None:
        seed = b"\x02" * 32
        wallet, _, metadata = self._make_wallet(seed)
        path = DerivationPath().to_string()
        wallet.add_account(metadata.wallet_id, "default", path)

        with self.assertRaises(RuntimeError):
            wallet.sign(metadata.wallet_id, path, b"payload")

        wallet.unlock(metadata.wallet_id, "pass")
        signature_one = wallet.sign(metadata.wallet_id, path, b"payload")
        signature_two = wallet.sign(metadata.wallet_id, path, b"payload")

        self.assertEqual(signature_one, signature_two)
        self.assertIsInstance(signature_one, str)

        wallet.lock()
        with self.assertRaises(RuntimeError):
            wallet.sign(metadata.wallet_id, path, b"payload")

    def test_wrong_passphrase_fails(self) -> None:
        seed = b"\x03" * 32
        wallet, _, metadata = self._make_wallet(seed)
        with self.assertRaises(ValueError):
            wallet.unlock(metadata.wallet_id, "wrong")

    def test_accounts_and_derivation_paths(self) -> None:
        seed = b"\x04" * 32
        wallet, _, metadata = self._make_wallet(seed)
        account_one = wallet.add_account(metadata.wallet_id, "first", "m/44'/0'/0'/0/0")
        account_two = wallet.add_account(metadata.wallet_id, "second", "m/44'/0'/1'/0/0")

        accounts = wallet.list_accounts(metadata.wallet_id)
        self.assertEqual(
            {account_one.account_id, account_two.account_id},
            {account.account_id for account in accounts},
        )

    def test_signing_changes_with_derivation_path(self) -> None:
        seed = b"\x05" * 32
        wallet, _, metadata = self._make_wallet(seed)
        wallet.unlock(metadata.wallet_id, "pass")
        sig_one = wallet.sign(metadata.wallet_id, "m/44'/0'/0'/0/0", b"payload")
        sig_two = wallet.sign(metadata.wallet_id, "m/44'/0'/1'/0/0", b"payload")
        self.assertNotEqual(sig_one, sig_two)

    def test_seed_export_fails_when_locked(self) -> None:
        seed = b"\x06" * 32
        wallet, _, metadata = self._make_wallet(seed)
        with self.assertRaises(RuntimeError):
            wallet.export_recovery_phrase(metadata.wallet_id)

    def test_seed_export_succeeds_when_unlocked(self) -> None:
        seed = b"\x07" * 32
        wallet, keystore, metadata = self._make_wallet(seed)
        wallet.unlock(metadata.wallet_id, "pass")
        phrase = wallet.export_recovery_phrase(metadata.wallet_id)
        self.assertIsInstance(phrase, str)
        self.assertTrue(phrase)

        record_json = json.dumps(keystore.load(metadata.wallet_id).to_dict())
        self.assertNotIn(phrase, record_json)

    def test_seed_export_is_deterministic(self) -> None:
        seed = b"\x08" * 32
        wallet, _, metadata = self._make_wallet(seed)
        wallet.unlock(metadata.wallet_id, "pass")
        phrase_one = wallet.export_recovery_phrase(metadata.wallet_id)
        phrase_two = wallet.export_recovery_phrase(metadata.wallet_id)
        self.assertEqual(phrase_one, phrase_two)

    def test_seed_export_does_not_mutate_state(self) -> None:
        seed = b"\x09" * 32
        wallet, keystore, metadata = self._make_wallet(seed)
        record_before = keystore.load(metadata.wallet_id).to_dict()
        wallet.unlock(metadata.wallet_id, "pass")
        _ = wallet.export_recovery_phrase(metadata.wallet_id)
        record_after = keystore.load(metadata.wallet_id).to_dict()
        self.assertEqual(record_before, record_after)


if __name__ == "__main__":
    unittest.main()
