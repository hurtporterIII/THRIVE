"""Thin AI advisory adapter for read-only analysis."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict


class AIAdapterError(RuntimeError):
    """Raised when AI advisory requests fail."""


def get_advice(api_key: str, context: Dict[str, Any]) -> str:
    if not api_key:
        raise AIAdapterError("API key is required.")

    safe_context = _sanitize_context(context)
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a read-only advisory assistant for a local, single-user "
                    "capital OS. Provide concise, cautious explanations of plans, "
                    "simulations, and capital snapshots. Do not give execution commands."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Review the following context and provide advisory notes, risks, "
                    "and clarifying questions if needed.\n\n"
                    + json.dumps(safe_context, indent=2)
                ),
            },
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:  # pragma: no cover - network dependent
        raise AIAdapterError("AI provider request failed.") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIAdapterError("AI provider returned invalid JSON.") from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIAdapterError("AI provider returned an unexpected response.") from exc


def _sanitize_context(context: Dict[str, Any]) -> Dict[str, Any]:
    forbidden = ("seed", "passphrase", "private", "secret", "mnemonic")

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: Dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(token in lowered for token in forbidden):
                    continue
                cleaned[key] = scrub(item)
            return cleaned
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(context)


def sanitize_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize_context(context)
