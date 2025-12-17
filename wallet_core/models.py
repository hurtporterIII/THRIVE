"""Domain models for the wallet core."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class DerivationPath:
    """BIP44-style derivation path components."""

    purpose: int = 44
    coin_type: int = 0
    account: int = 0
    change: int = 0
    address_index: int = 0

    def to_string(self) -> str:
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
class WalletMetadata:
    wallet_id: str
    label: str
    created_at: str


@dataclass(frozen=True)
class Account:
    account_id: str
    label: str
    derivation_path: str


@dataclass(frozen=True)
class WalletRecord:
    metadata: WalletMetadata
    accounts: Tuple[Account, ...]
    encrypted_seed: EncryptedPayload

    def to_dict(self) -> Dict[str, object]:
        return {
            "metadata": {
                "wallet_id": self.metadata.wallet_id,
                "label": self.metadata.label,
                "created_at": self.metadata.created_at,
            },
            "accounts": [
                {
                    "account_id": account.account_id,
                    "label": account.label,
                    "derivation_path": account.derivation_path,
                }
                for account in self.accounts
            ],
            "encrypted_seed": self.encrypted_seed.to_dict(),
        }

    @staticmethod
    def from_dict(data: Dict[str, object]) -> "WalletRecord":
        meta = data["metadata"]
        metadata = WalletMetadata(
            wallet_id=meta["wallet_id"],
            label=meta["label"],
            created_at=meta["created_at"],
        )
        accounts = tuple(
            Account(
                account_id=entry["account_id"],
                label=entry["label"],
                derivation_path=entry["derivation_path"],
            )
            for entry in data.get("accounts", [])
        )
        encrypted_seed = EncryptedPayload.from_dict(data["encrypted_seed"])
        return WalletRecord(
            metadata=metadata,
            accounts=accounts,
            encrypted_seed=encrypted_seed,
        )
