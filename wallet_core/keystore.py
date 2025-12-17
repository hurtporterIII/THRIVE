"""Local keystore persistence for wallet records."""

from pathlib import Path
from typing import Iterable, Protocol, Tuple
import json

from .models import WalletMetadata, WalletRecord


class KeyStore(Protocol):
    def store(self, record: WalletRecord) -> None:
        ...

    def load(self, wallet_id: str) -> WalletRecord:
        ...

    def list_metadata(self) -> Tuple[WalletMetadata, ...]:
        ...


class FileKeyStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def store(self, record: WalletRecord) -> None:
        records = {item.metadata.wallet_id: item for item in self._read_all()}
        records[record.metadata.wallet_id] = record
        self._write_all(records.values())

    def load(self, wallet_id: str) -> WalletRecord:
        for record in self._read_all():
            if record.metadata.wallet_id == wallet_id:
                return record
        raise KeyError(f"Unknown wallet_id: {wallet_id}")

    def list_metadata(self) -> Tuple[WalletMetadata, ...]:
        return tuple(record.metadata for record in self._read_all())

    def _read_all(self) -> Tuple[WalletRecord, ...]:
        if not self._path.exists():
            return ()
        data = json.loads(self._path.read_text())
        return tuple(WalletRecord.from_dict(item) for item in data)

    def _write_all(self, records: Iterable[WalletRecord]) -> None:
        payload = [record.to_dict() for record in records]
        self._path.write_text(json.dumps(payload, indent=2))
