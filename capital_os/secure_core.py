"""Secure core interfaces and local key management."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Protocol, Tuple
import base64
import hashlib
import hmac
import json
import secrets


@dataclass(frozen=True)
class DerivationPath:
    """BIP44-style derivation path components."""

    purpose: int = 44
    coin_type: int = 0
    account: int = 0
    change: int = 0
    address_index: int = 0

    def to_bip44(self) -> str:
        return (
            f"m/{self.purpose}'/{self.coin_type}'/"
            f"{self.account}'/{self.change}/{self.address_index}"
        )


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: str
    salt: str
    nonce: str
    mac: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "ciphertext": self.ciphertext,
            "salt": self.salt,
            "nonce": self.nonce,
            "mac": self.mac,
        }

    @staticmethod
    def from_dict(data: Dict[str, str]) -> "EncryptedPayload":
        return EncryptedPayload(
            ciphertext=data["ciphertext"],
            salt=data["salt"],
            nonce=data["nonce"],
            mac=data["mac"],
        )


@dataclass(frozen=True)
class KeyMetadata:
    key_id: str
    label: str
    public_key: str
    derivation_path: str
    created_at: str


@dataclass(frozen=True)
class KeyRecord:
    metadata: KeyMetadata
    encrypted_private_key: EncryptedPayload

    def to_dict(self) -> Dict[str, object]:
        return {
            "metadata": {
                "key_id": self.metadata.key_id,
                "label": self.metadata.label,
                "public_key": self.metadata.public_key,
                "derivation_path": self.metadata.derivation_path,
                "created_at": self.metadata.created_at,
            },
            "encrypted_private_key": self.encrypted_private_key.to_dict(),
        }

    @staticmethod
    def from_dict(data: Dict[str, object]) -> "KeyRecord":
        meta = data["metadata"]
        metadata = KeyMetadata(
            key_id=meta["key_id"],
            label=meta["label"],
            public_key=meta["public_key"],
            derivation_path=meta["derivation_path"],
            created_at=meta["created_at"],
        )
        encrypted = EncryptedPayload.from_dict(data["encrypted_private_key"])
        return KeyRecord(metadata=metadata, encrypted_private_key=encrypted)


class Encryptor(Protocol):
    def encrypt(self, plaintext: bytes, passphrase: str) -> EncryptedPayload:
        ...

    def decrypt(self, payload: EncryptedPayload, passphrase: str) -> bytes:
        ...


class PassphraseEncryptor:
    """Minimal deterministic stream cipher for local key storage."""

    def __init__(self, iterations: int = 200_000) -> None:
        self._iterations = iterations

    def encrypt(self, plaintext: bytes, passphrase: str) -> EncryptedPayload:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(16)
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


class KeyStore(Protocol):
    def store(self, record: KeyRecord) -> None:
        ...

    def load(self, key_id: str) -> KeyRecord:
        ...

    def list_metadata(self) -> Tuple[KeyMetadata, ...]:
        ...


class FileKeyStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def store(self, record: KeyRecord) -> None:
        records = {item.metadata.key_id: item for item in self._read_all()}
        records[record.metadata.key_id] = record
        self._write_all(records.values())

    def load(self, key_id: str) -> KeyRecord:
        for record in self._read_all():
            if record.metadata.key_id == key_id:
                return record
        raise KeyError(f"Unknown key_id: {key_id}")

    def list_metadata(self) -> Tuple[KeyMetadata, ...]:
        return tuple(record.metadata for record in self._read_all())

    def _read_all(self) -> Tuple[KeyRecord, ...]:
        if not self._path.exists():
            return ()
        data = json.loads(self._path.read_text())
        return tuple(KeyRecord.from_dict(item) for item in data)

    def _write_all(self, records: Iterable[KeyRecord]) -> None:
        payload = [record.to_dict() for record in records]
        self._path.write_text(json.dumps(payload, indent=2))


class SecureCore:
    """Local-only key management and signing surface."""

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

    def generate_hd_wallet(
        self,
        label: str,
        passphrase: str,
        derivation_path: Optional[str] = None,
    ) -> KeyMetadata:
        path = derivation_path or DerivationPath().to_bip44()
        seed = self._entropy_provider(32)
        private_key = _derive_private_key(seed, path)
        public_key = _derive_public_key(private_key)
        key_id = _derive_key_id(public_key, path)
        created_at = self._time_provider()

        encrypted = self._encryptor.encrypt(private_key, passphrase)
        metadata = KeyMetadata(
            key_id=key_id,
            label=label,
            public_key=public_key,
            derivation_path=path,
            created_at=created_at,
        )
        self._keystore.store(KeyRecord(metadata=metadata, encrypted_private_key=encrypted))
        return metadata

    def list_keys(self) -> Tuple[KeyMetadata, ...]:
        return self._keystore.list_metadata()

    def get_public_key(self, key_id: str) -> str:
        return self._keystore.load(key_id).metadata.public_key

    def sign(self, key_id: str, passphrase: str, payload: bytes) -> str:
        record = self._keystore.load(key_id)
        private_key = self._encryptor.decrypt(record.encrypted_private_key, passphrase)
        signature = hmac.new(private_key, payload, hashlib.sha256).hexdigest()
        return signature


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_private_key(seed: bytes, path: str) -> bytes:
    return hmac.new(seed, path.encode("utf-8"), hashlib.sha256).digest()


def _derive_public_key(private_key: bytes) -> str:
    return hashlib.sha256(private_key).hexdigest()


def _derive_key_id(public_key: str, path: str) -> str:
    return hashlib.sha256(f"{public_key}:{path}".encode("utf-8")).hexdigest()[:16]
