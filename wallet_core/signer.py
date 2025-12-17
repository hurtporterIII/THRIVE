"""Wallet core signing surface with explicit lock/unlock lifecycle."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, Tuple
import base64
import hashlib
import hmac
import secrets

from .keystore import KeyStore
from .models import Account, DerivationPath, EncryptedPayload, WalletMetadata, WalletRecord


class Encryptor(Protocol):
    def encrypt(self, plaintext: bytes, passphrase: str) -> EncryptedPayload:
        ...

    def decrypt(self, payload: EncryptedPayload, passphrase: str) -> bytes:
        ...


class PassphraseEncryptor:
    """Deterministic stream cipher wrapper for local key storage."""

    def __init__(
        self,
        iterations: int = 200_000,
        salt_provider: Optional[Callable[[int], bytes]] = None,
        nonce_provider: Optional[Callable[[int], bytes]] = None,
    ) -> None:
        self._iterations = iterations
        self._salt_provider = salt_provider or secrets.token_bytes
        self._nonce_provider = nonce_provider or secrets.token_bytes

    def encrypt(self, plaintext: bytes, passphrase: str) -> EncryptedPayload:
        salt = self._salt_provider(16)
        nonce = self._nonce_provider(16)
        key = self._derive_key(passphrase, salt, nonce)
        keystream = self._keystream(key, nonce, len(plaintext))
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, keystream))
        mac = hmac.new(key, ciphertext, hashlib.sha256).digest()
        return EncryptedPayload(
            ciphertext=_b64encode(ciphertext),
            salt=_b64encode(salt),
            nonce=_b64encode(nonce),
            mac=_b64encode(mac),
        )

    def decrypt(self, payload: EncryptedPayload, passphrase: str) -> bytes:
        salt = _b64decode(payload.salt)
        nonce = _b64decode(payload.nonce)
        ciphertext = _b64decode(payload.ciphertext)
        expected_mac = _b64decode(payload.mac)
        key = self._derive_key(passphrase, salt, nonce)
        actual_mac = hmac.new(key, ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(actual_mac, expected_mac):
            raise ValueError("Invalid passphrase or corrupted payload.")
        keystream = self._keystream(key, nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, keystream))

    def _derive_key(self, passphrase: str, salt: bytes, nonce: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt + nonce,
            self._iterations,
            dklen=32,
        )

    def _keystream(self, key: bytes, nonce: bytes, length: int) -> bytes:
        blocks = []
        counter = 0
        while sum(len(block) for block in blocks) < length:
            counter_bytes = counter.to_bytes(4, "big")
            blocks.append(hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest())
            counter += 1
        return b"".join(blocks)[:length]


@dataclass(frozen=True)
class WalletStatus:
    wallet_id: str
    unlocked: bool


class WalletCore:
    """Local-only wallet core with deterministic signing."""

    def __init__(
        self,
        keystore: KeyStore,
        encryptor: Encryptor,
        time_provider: Optional[Callable[[], str]] = None,
        entropy_provider: Optional[Callable[[int], bytes]] = None,
    ) -> None:
        self._keystore = keystore
        self._encryptor = encryptor
        self._time_provider = time_provider or _utc_timestamp
        self._entropy_provider = entropy_provider or secrets.token_bytes
        self._unlocked_wallet_id: Optional[str] = None
        self._unlocked_seed: Optional[bytes] = None

    def create_wallet(self, label: str, passphrase: str) -> WalletMetadata:
        seed = self._entropy_provider(32)
        wallet_id = _derive_wallet_id(seed)
        created_at = self._time_provider()
        encrypted_seed = self._encryptor.encrypt(seed, passphrase)
        metadata = WalletMetadata(
            wallet_id=wallet_id,
            label=label,
            created_at=created_at,
        )
        record = WalletRecord(metadata=metadata, accounts=(), encrypted_seed=encrypted_seed)
        self._keystore.store(record)
        return metadata

    def list_wallets(self) -> Tuple[WalletMetadata, ...]:
        return self._keystore.list_metadata()

    def add_account(
        self, wallet_id: str, label: str, derivation_path: Optional[str] = None
    ) -> Account:
        record = self._keystore.load(wallet_id)
        path = derivation_path or DerivationPath().to_string()
        account_id = _derive_account_id(wallet_id, path)
        if any(account.account_id == account_id for account in record.accounts):
            raise ValueError("Account already exists for derivation path.")
        account = Account(account_id=account_id, label=label, derivation_path=path)
        updated = WalletRecord(
            metadata=record.metadata,
            accounts=record.accounts + (account,),
            encrypted_seed=record.encrypted_seed,
        )
        self._keystore.store(updated)
        return account

    def list_accounts(self, wallet_id: str) -> Tuple[Account, ...]:
        return self._keystore.load(wallet_id).accounts

    def unlock(self, wallet_id: str, passphrase: str) -> WalletStatus:
        record = self._keystore.load(wallet_id)
        seed = self._encryptor.decrypt(record.encrypted_seed, passphrase)
        self._unlocked_seed = seed
        self._unlocked_wallet_id = wallet_id
        return WalletStatus(wallet_id=wallet_id, unlocked=True)

    def lock(self) -> None:
        self._unlocked_seed = None
        self._unlocked_wallet_id = None

    def status(self, wallet_id: str) -> WalletStatus:
        return WalletStatus(
            wallet_id=wallet_id,
            unlocked=self._unlocked_wallet_id == wallet_id and self._unlocked_seed is not None,
        )

    def get_public_key(self, wallet_id: str, derivation_path: str) -> str:
        seed = self._require_unlocked(wallet_id)
        private_key = _derive_private_key(seed, derivation_path)
        return _derive_public_key(private_key)

    def sign(self, wallet_id: str, derivation_path: str, payload: bytes) -> str:
        seed = self._require_unlocked(wallet_id)
        private_key = _derive_private_key(seed, derivation_path)
        return hmac.new(private_key, payload, hashlib.sha256).hexdigest()

    def _require_unlocked(self, wallet_id: str) -> bytes:
        if self._unlocked_seed is None or self._unlocked_wallet_id != wallet_id:
            raise RuntimeError("Wallet is locked.")
        return self._unlocked_seed


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_wallet_id(seed: bytes) -> str:
    return hashlib.sha256(seed).hexdigest()[:16]


def _derive_account_id(wallet_id: str, path: str) -> str:
    return hashlib.sha256(f"{wallet_id}:{path}".encode("utf-8")).hexdigest()[:16]


def _derive_private_key(seed: bytes, path: str) -> bytes:
    return hmac.new(seed, path.encode("utf-8"), hashlib.sha256).digest()


def _derive_public_key(private_key: bytes) -> str:
    return hashlib.sha256(private_key).hexdigest()
