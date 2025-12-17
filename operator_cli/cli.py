"""Operator CLI for the Capital OS."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from execution_adapter.ethereum.adapter import AdapterError, plan_to_payloads
from execution_adapter.ethereum.simulator import SimulationError, simulate
from execution_controller.controller import (
    ExecutionBlockedError,
    ExecutionController,
    ModeTransitionError,
    PolicyViolationError,
)
from execution_controller.modes import ExecutionMode
from execution_controller.policy import GuardPolicy
from execution_engine.models import (
    ActionType,
    CapitalExposure,
    CapitalState,
    ExecutionIntent,
    ExecutionPlan,
    ExecutionStep,
    SignatureRequirement,
)
from execution_engine.planner import PlanValidationError, UnimplementedIntentError, validate_plan
from execution_engine.planner import ExecutionPlanner
from wallet_core.keystore import FileKeyStore
from wallet_core.models import DerivationPath, WalletRecord
from wallet_core.signer import PassphraseEncryptor, WalletCore

try:
    from colorama import Fore, Style, init as colorama_init
except ImportError:  # pragma: no cover - optional dependency
    Fore = None
    Style = None
    colorama_init = None

_PROOF_MESSAGE = "WALLET PROOF CHECK"
_MEMORY_KEYSTORES: dict[str, dict[str, WalletRecord]] = {}
_ACTIVE_ACCOUNTS: dict[str, dict[str, str]] = {}
_COLOR_READY = False
_COLOR_DISABLED = os.environ.get("NO_COLOR") is not None

if colorama_init:
    colorama_init()
    _COLOR_READY = True


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return _dashboard()

    parser = argparse.ArgumentParser(prog="capital-os")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wallet_parser = subparsers.add_parser("wallet")
    wallet_sub = wallet_parser.add_subparsers(dest="wallet_command", required=True)

    wallet_init = wallet_sub.add_parser("init")
    wallet_init.add_argument("--keystore", required=True)
    wallet_init.add_argument("--label", required=True)
    wallet_init.add_argument("--passphrase", required=True)
    wallet_init.set_defaults(func=_wallet_init)

    wallet_unlock = wallet_sub.add_parser("unlock")
    wallet_unlock.add_argument("--keystore", required=True)
    wallet_unlock.add_argument("--wallet-id", required=True)
    wallet_unlock.add_argument("--passphrase", required=True)
    wallet_unlock.set_defaults(func=_wallet_unlock)

    wallet_lock = wallet_sub.add_parser("lock")
    wallet_lock.add_argument("--keystore", required=True)
    wallet_lock.set_defaults(func=_wallet_lock)

    wallet_accounts = wallet_sub.add_parser("accounts")
    wallet_accounts.add_argument("--keystore", required=True)
    wallet_accounts.add_argument("--wallet-id", required=True)
    wallet_accounts.add_argument("--show-path", action="store_true")
    wallet_accounts.set_defaults(func=_wallet_accounts)

    wallet_select = wallet_sub.add_parser("select")
    wallet_select.add_argument("--keystore", required=True)
    wallet_select.add_argument("--wallet-id", required=True)
    wallet_select.add_argument("--account-id", required=True)
    wallet_select.set_defaults(func=_wallet_select)

    wallet_show = wallet_sub.add_parser("show")
    wallet_show.add_argument("--keystore", required=True)
    wallet_show.add_argument("--wallet-id", required=True)
    wallet_show.add_argument("--passphrase")
    wallet_show.add_argument("--account-id")
    wallet_show.add_argument("--derivation-path")
    wallet_show.set_defaults(func=_wallet_show)

    wallet_address = wallet_sub.add_parser("address")
    wallet_address.add_argument("--keystore", required=True)
    wallet_address.add_argument("--wallet-id", required=True)
    wallet_address.add_argument("--passphrase")
    wallet_address.add_argument("--account-id")
    wallet_address.add_argument("--derivation-path")
    wallet_address.set_defaults(func=_wallet_address)

    wallet_seed = wallet_sub.add_parser("seed")
    wallet_seed.add_argument("--keystore")
    wallet_seed.add_argument("--wallet-id")
    wallet_seed.add_argument("--passphrase")
    wallet_seed.add_argument("--json", action="store_true")
    wallet_seed.set_defaults(func=_wallet_seed)

    wallet_backup = wallet_sub.add_parser("backup")
    wallet_backup.add_argument("--keystore", required=True)
    wallet_backup.add_argument("--wallet-id", required=True)
    wallet_backup.set_defaults(func=_wallet_backup)

    state_parser = subparsers.add_parser("state")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    state_show = state_sub.add_parser("show")
    _add_state_args(state_show)
    state_show.set_defaults(func=_state_show)

    plan_parser = subparsers.add_parser("plan")
    plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_sub.add_parser("create")
    _add_plan_args(plan_create)
    plan_create.set_defaults(func=_plan_create)

    simulate_parser = subparsers.add_parser("simulate")
    simulate_parser.add_argument("--plan", required=True)
    simulate_parser.add_argument("--json", action="store_true")
    simulate_parser.set_defaults(func=_simulate_plan)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--keystore", required=True)
    status_parser.add_argument("--wallet-id", required=True)
    status_parser.add_argument("--passphrase")
    status_parser.add_argument("--account-id")
    status_parser.add_argument("--derivation-path")
    status_parser.add_argument("--snapshot-id")
    status_parser.add_argument("--exposure", action="append")
    status_parser.add_argument("--plan")
    status_parser.add_argument("--dry-run")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=_status)

    prove_parser = subparsers.add_parser("prove")
    prove_parser.add_argument("--keystore", required=True)
    prove_parser.add_argument("--wallet-id", required=True)
    prove_parser.add_argument("--passphrase")
    prove_parser.add_argument("--account-id")
    prove_parser.add_argument("--derivation-path")
    prove_parser.add_argument("--json", action="store_true")
    prove_parser.set_defaults(func=_prove)

    account_parser = subparsers.add_parser("account")
    account_sub = account_parser.add_subparsers(dest="account_command", required=True)

    account_new = account_sub.add_parser("new")
    account_new.add_argument("--keystore", required=True)
    account_new.add_argument("--wallet-id", required=True)
    account_new.add_argument("--label", required=True)
    account_new.add_argument("--derivation-path")
    account_new.set_defaults(func=_account_new)

    account_switch = account_sub.add_parser("switch")
    account_switch.add_argument("--keystore", required=True)
    account_switch.add_argument("--wallet-id", required=True)
    account_switch.add_argument("--account-id", required=True)
    account_switch.set_defaults(func=_wallet_select)

    account_rename = account_sub.add_parser("rename")
    account_rename.add_argument("--keystore", required=True)
    account_rename.add_argument("--wallet-id", required=True)
    account_rename.add_argument("--account-id", required=True)
    account_rename.add_argument("--label", required=True)
    account_rename.set_defaults(func=_account_rename)

    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--plan", required=True)
    execute_parser.add_argument("--mode", choices=("manual", "guarded"), required=True)
    execute_parser.add_argument("--arm", action="store_true")
    execute_parser.add_argument("--yes", action="store_true")
    execute_parser.add_argument("--allowed-action", action="append", default=[])
    execute_parser.add_argument("--allowed-asset", action="append")
    execute_parser.set_defaults(func=_execute_plan)

    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (
        ValueError,
        ExecutionBlockedError,
        PolicyViolationError,
        ModeTransitionError,
        PlanValidationError,
        UnimplementedIntentError,
        AdapterError,
        SimulationError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _wallet_init(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    metadata = wallet.create_wallet(label=args.label, passphrase=args.passphrase)
    account = wallet.add_account(
        metadata.wallet_id,
        "default",
        DerivationPath().to_string(),
    )
    _set_active_account(args.keystore, metadata.wallet_id, account.account_id)
    print(metadata.wallet_id)
    return 0


def _wallet_unlock(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    status = wallet.unlock(wallet_id=args.wallet_id, passphrase=args.passphrase)
    print(f"{status.wallet_id} unlocked")
    return 0


def _wallet_lock(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    wallet.lock()
    print("locked")
    return 0


def _wallet_accounts(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    accounts = wallet.list_accounts(args.wallet_id)
    for account in accounts:
        if args.show_path:
            print(f"{account.account_id} {account.label} {account.derivation_path}")
        else:
            print(f"{account.account_id} {account.label}")
    return 0


def _wallet_select(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    accounts = wallet.list_accounts(args.wallet_id)
    for account in accounts:
        if account.account_id == args.account_id:
            _set_active_account(args.keystore, args.wallet_id, args.account_id)
            print(f"active {account.account_id}")
            return 0
    raise ValueError("Account not found.")


def _wallet_show(args: argparse.Namespace) -> int:
    status_args = argparse.Namespace(
        keystore=args.keystore,
        wallet_id=args.wallet_id,
        passphrase=args.passphrase,
        account_id=args.account_id,
        derivation_path=args.derivation_path,
        snapshot_id=None,
        exposure=None,
        plan=None,
        dry_run=None,
        json=False,
    )
    return _status(status_args)


def _wallet_address(args: argparse.Namespace) -> int:
    if not args.passphrase:
        raise ExecutionBlockedError("Wallet is locked.")
    keystore = _get_keystore(args.keystore)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    wallet.unlock(wallet_id=args.wallet_id, passphrase=args.passphrase)
    account_label, derivation_path = _select_account_info(
        wallet, args.keystore, args.wallet_id, args.account_id, args.derivation_path
    )
    address = wallet.get_public_key(args.wallet_id, derivation_path)
    _print_header("Wallet Address")
    _print_key_value("Account", account_label)
    _print_copy_block("Address", address)
    return 0


def _wallet_seed(args: argparse.Namespace) -> int:
    keystore_path = args.keystore or _prompt(input, "Keystore path")
    wallet_id = args.wallet_id or _prompt(input, "Wallet ID")
    passphrase = args.passphrase
    if passphrase is None:
        passphrase = getpass.getpass("Passphrase: ")
    if not passphrase:
        raise ExecutionBlockedError("Wallet is locked.")

    keystore = _get_keystore(keystore_path)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    wallet.unlock(wallet_id=wallet_id, passphrase=passphrase)
    phrase = wallet.export_recovery_phrase(wallet_id)

    output = {
        "wallet_id": wallet_id,
        "seed_phrase": phrase,
        "warning": "Anyone with this phrase controls your funds.",
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        _print_header("Recovery Phrase")
        _print_warning(output["warning"])
        _print_copy_block("Seed Phrase", phrase)
    return 0


def _wallet_backup(args: argparse.Namespace) -> int:
    _print_header("Wallet Backup")
    _print_copy_block("Keystore Path", args.keystore)
    active_path = _active_accounts_path(args.keystore)
    _print_copy_block("Active Accounts File", str(active_path))
    return 0


def _account_new(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    path = args.derivation_path or DerivationPath().to_string()
    account = wallet.add_account(args.wallet_id, args.label, path)
    _set_active_account(args.keystore, args.wallet_id, account.account_id)
    _print_header("Account Created")
    _print_key_value("Account", account.account_id)
    _print_key_value("Label", account.label)
    return 0


def _account_rename(args: argparse.Namespace) -> int:
    _print_header("Account Rename")
    print("Rename is not available in this build.")
    return 0


def _state_show(args: argparse.Namespace) -> int:
    capital_state = _build_capital_state(args)
    print(json.dumps(_capital_state_to_dict(capital_state), indent=2))
    return 0


def _plan_create(args: argparse.Namespace) -> int:
    intent = _build_intent(args)
    capital_state = _build_capital_state(args)
    planner = ExecutionPlanner()
    plan = planner.plan(intent, capital_state)
    print(json.dumps(_plan_to_dict(plan), indent=2))
    return 0


def _simulate_plan(args: argparse.Namespace) -> int:
    plan = _load_plan(args.plan)
    payloads = plan_to_payloads(plan)
    dry_run = simulate(payloads)
    if args.json:
        output = {
            "payloads": [asdict(payload) for payload in payloads],
            "dry_run": asdict(dry_run),
        }
        print(json.dumps(output, indent=2))
    else:
        _print_header("Ethereum Simulation")
        _print_key_value("Transactions", str(len(payloads)))
        for payload in payloads:
            payload_text = json.dumps(asdict(payload), indent=2)
            _print_copy_block("Tx Payload", payload_text)
        _print_key_value("Dry Run Success", str(dry_run.success))
        _print_key_value("Total Gas Used", str(dry_run.total_gas_used))
        _print_key_value("Total Cost (wei)", str(dry_run.total_cost_wei))
        if dry_run.notes:
            _print_list("Notes", list(dry_run.notes))
    return 0


def _execute_plan(args: argparse.Namespace) -> int:
    plan = _load_plan(args.plan)

    controller = ExecutionController()
    if args.mode == "manual":
        controller.set_mode(ExecutionMode.MANUAL)
    else:
        policy = _build_policy(args)
        controller.set_mode(ExecutionMode.GUARDED, policy=policy)

    if not args.arm:
        raise ExecutionBlockedError("Execution must be armed explicitly.")
    controller.arm()

    if not args.yes:
        if not _confirm("Confirm execution? [y/N]: "):
            raise ExecutionBlockedError("Execution confirmation denied.")

    confirm_step = None
    if args.mode == "manual":
        confirm_step = _confirm_step if args.yes else _prompt_step

    decisions = controller.evaluate_plan(plan, confirm_step=confirm_step)
    print(
        json.dumps(
            {
                "mode": args.mode,
                "decisions": [asdict(decision) for decision in decisions],
            },
            indent=2,
        )
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    keystore = _get_keystore(args.keystore)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    wallet_state = "LOCKED"
    if args.passphrase:
        wallet.unlock(wallet_id=args.wallet_id, passphrase=args.passphrase)
        wallet_state = "UNLOCKED"

    account_label, derivation_path = _select_account_info(
        wallet, args.keystore, args.wallet_id, args.account_id, args.derivation_path
    )
    address = "LOCKED"
    if wallet_state == "UNLOCKED":
        address = wallet.get_public_key(args.wallet_id, derivation_path)

    exposures = ()
    capital_total = None
    if args.exposure and args.snapshot_id:
        exposures = _parse_exposures(args.exposure)
        capital_total = sum(item.quantity for item in exposures)

    plan_summary = _maybe_plan_summary(args.plan) if args.plan else None
    dry_run_summary = _maybe_dry_run_summary(args.dry_run) if args.dry_run else None

    output = {
        "wallet_state": wallet_state,
        "active_account": account_label,
        "active_address": address,
        "capital_total": capital_total,
        "exposures": [
            {"asset_code": exposure.asset_code, "quantity": exposure.quantity}
            for exposure in exposures
        ],
        "execution_mode": ExecutionMode.SAFE.value,
        "last_plan": plan_summary,
        "last_dry_run": dry_run_summary,
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        _print_status(output)
    return 0


def _prove(args: argparse.Namespace) -> int:
    if not args.passphrase:
        raise ExecutionBlockedError("Wallet is locked.")

    keystore = _get_keystore(args.keystore)
    wallet = WalletCore(keystore=keystore, encryptor=PassphraseEncryptor())
    wallet.unlock(wallet_id=args.wallet_id, passphrase=args.passphrase)
    account_label, derivation_path = _select_account_info(
        wallet, args.keystore, args.wallet_id, args.account_id, args.derivation_path
    )
    address = wallet.get_public_key(args.wallet_id, derivation_path)
    signature = wallet.sign(args.wallet_id, derivation_path, _PROOF_MESSAGE.encode("utf-8"))
    verification = signature == wallet.sign(
        args.wallet_id, derivation_path, _PROOF_MESSAGE.encode("utf-8")
    )

    output = {
        "message": _PROOF_MESSAGE,
        "account": account_label,
        "address": address,
        "signature": signature,
        "verification": "PASS" if verification else "FAIL",
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        _print_header("Proof of Custody")
        _print_key_value("Account", output["account"])
        _print_copy_block("Message", output["message"])
        _print_copy_block("Address", output["address"])
        _print_copy_block("Signature", output["signature"])
        _print_key_value("Verification", output["verification"])
    return 0


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--action", required=True)
    parser.add_argument("--from-asset", required=True)
    parser.add_argument("--to-asset", required=True)
    parser.add_argument("--amount", required=True, type=float)
    _add_state_args(parser)


def _add_state_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--exposure", action="append", required=True)


def _build_intent(args: argparse.Namespace) -> ExecutionIntent:
    action_type = _parse_action(args.action)
    return ExecutionIntent(
        action_type=action_type,
        from_asset=args.from_asset,
        to_asset=args.to_asset,
        amount=args.amount,
    )


def _build_capital_state(args: argparse.Namespace) -> CapitalState:
    exposures = _parse_exposures(args.exposure)
    return CapitalState(
        snapshot_id=args.snapshot_id,
        exposures=exposures,
    )


def _parse_exposures(values: Iterable[str]) -> Tuple[CapitalExposure, ...]:
    exposures = []
    for raw in values:
        if "=" not in raw:
            raise ValueError("Exposure must be formatted as ASSET=AMOUNT.")
        asset, amount = raw.split("=", 1)
        if not asset:
            raise ValueError("Exposure asset code is required.")
        exposures.append(CapitalExposure(asset_code=asset, quantity=float(amount)))
    return tuple(sorted(exposures, key=lambda item: item.asset_code))


def _parse_action(value: str) -> ActionType:
    normalized = value.strip().upper()
    for action in ActionType:
        if action.value == normalized:
            return action
    raise ValueError(f"Unsupported action type: {value}")


def _load_plan(plan_source: str) -> ExecutionPlan:
    if plan_source == "-":
        payload = json.loads(sys.stdin.read())
    else:
        payload = json.loads(Path(plan_source).read_text())
    plan = _plan_from_dict(payload)
    validate_plan(plan)
    return plan


def _plan_to_dict(plan: ExecutionPlan) -> dict:
    return {
        "intent": {
            "action_type": plan.intent.action_type.value,
            "from_asset": plan.intent.from_asset,
            "to_asset": plan.intent.to_asset,
            "amount": plan.intent.amount,
            "notes": list(plan.intent.notes),
        },
        "capital_state": _capital_state_to_dict(plan.capital_state),
        "steps": [
            {
                "sequence": step.sequence,
                "action_type": step.action_type.value,
                "from_asset": step.from_asset,
                "to_asset": step.to_asset,
                "amount": step.amount,
                "rationale": step.rationale,
            }
            for step in plan.steps
        ],
        "assumptions": list(plan.assumptions),
        "failure_modes": list(plan.failure_modes),
        "required_signatures": [
            {
                "signer_reference": req.signer_reference,
                "purpose": req.purpose,
            }
            for req in plan.required_signatures
        ],
        "estimated_cost": plan.estimated_cost,
    }


def _capital_state_to_dict(capital_state: CapitalState) -> dict:
    return {
        "snapshot_id": capital_state.snapshot_id,
        "exposures": [
            {"asset_code": exposure.asset_code, "quantity": exposure.quantity}
            for exposure in capital_state.exposures
        ],
        "notes": list(capital_state.notes),
    }


def _plan_from_dict(data: dict) -> ExecutionPlan:
    intent_data = data["intent"]
    intent = ExecutionIntent(
        action_type=_parse_action(intent_data["action_type"]),
        from_asset=intent_data["from_asset"],
        to_asset=intent_data["to_asset"],
        amount=float(intent_data["amount"]),
        notes=tuple(intent_data.get("notes", [])),
    )
    state_data = data["capital_state"]
    capital_state = CapitalState(
        snapshot_id=state_data["snapshot_id"],
        exposures=tuple(
            CapitalExposure(
                asset_code=entry["asset_code"],
                quantity=float(entry["quantity"]),
            )
            for entry in state_data["exposures"]
        ),
        notes=tuple(state_data.get("notes", [])),
    )
    steps = tuple(
        ExecutionStep(
            sequence=int(step["sequence"]),
            action_type=_parse_action(step["action_type"]),
            from_asset=step["from_asset"],
            to_asset=step["to_asset"],
            amount=float(step["amount"]),
            rationale=step["rationale"],
        )
        for step in data["steps"]
    )
    required_signatures = tuple(
        SignatureRequirement(
            signer_reference=req["signer_reference"],
            purpose=req["purpose"],
        )
        for req in data["required_signatures"]
    )
    return ExecutionPlan(
        intent=intent,
        capital_state=capital_state,
        steps=steps,
        assumptions=tuple(data["assumptions"]),
        failure_modes=tuple(data["failure_modes"]),
        required_signatures=required_signatures,
        estimated_cost=float(data["estimated_cost"]),
    )


def _build_policy(args: argparse.Namespace) -> GuardPolicy:
    if not args.allowed_action:
        raise PolicyViolationError("Guarded mode requires allowed actions.")
    actions = tuple(_parse_action(action) for action in args.allowed_action)
    assets = tuple(args.allowed_asset) if args.allowed_asset else None
    return GuardPolicy(allowed_action_types=actions, allowed_assets=assets)


def _confirm(prompt: str) -> bool:
    response = input(prompt)
    return response.strip().lower() in {"y", "yes"}


def _confirm_step(step: ExecutionStep) -> bool:
    return True


def _prompt_step(step: ExecutionStep) -> bool:
    return _confirm(
        f"Confirm step {step.sequence} {step.action_type.value} "
        f"{step.amount} {step.from_asset}->{step.to_asset}? [y/N]: "
    )


def _get_keystore(path_value: str):
    if path_value.startswith("mem://"):
        store = _MEMORY_KEYSTORES.setdefault(path_value, {})
        return _InMemoryKeyStore(store)
    return FileKeyStore(Path(path_value))


def _select_account_info(
    wallet: WalletCore,
    keystore_path: str,
    wallet_id: str,
    account_id: Optional[str],
    derivation_path: Optional[str],
) -> Tuple[str, str]:
    accounts = wallet.list_accounts(wallet_id)
    if account_id:
        for account in accounts:
            if account.account_id == account_id:
                return account.label, account.derivation_path
        raise ValueError("Account not found.")
    if derivation_path:
        return "custom", derivation_path
    active_id = _get_active_account(keystore_path, wallet_id)
    if active_id:
        for account in accounts:
            if account.account_id == active_id:
                return account.label, account.derivation_path
    if accounts:
        account = accounts[0]
        return account.label, account.derivation_path
    return "NONE", DerivationPath().to_string()


def _maybe_plan_summary(plan_source: str) -> Optional[dict]:
    plan = _load_plan(plan_source)
    return {
        "action_type": plan.intent.action_type.value,
        "from_asset": plan.intent.from_asset,
        "to_asset": plan.intent.to_asset,
        "amount": plan.intent.amount,
        "steps": len(plan.steps),
    }


def _maybe_dry_run_summary(source: str) -> Optional[dict]:
    payload = json.loads(Path(source).read_text())
    dry_run = payload.get("dry_run", {})
    return {
        "success": dry_run.get("success"),
        "total_gas_used": dry_run.get("total_gas_used"),
        "total_cost_wei": dry_run.get("total_cost_wei"),
    }


class _InMemoryKeyStore:
    def __init__(self, store: dict[str, WalletRecord]) -> None:
        self._store = store

    def store(self, record: WalletRecord) -> None:
        self._store[record.metadata.wallet_id] = record

    def load(self, wallet_id: str) -> WalletRecord:
        if wallet_id not in self._store:
            raise KeyError(f"Unknown wallet_id: {wallet_id}")
        return self._store[wallet_id]

    def list_metadata(self) -> Tuple:
        return tuple(record.metadata for record in self._store.values())


def _active_account_store(path_value: str) -> dict[str, str]:
    if path_value.startswith("mem://"):
        return _ACTIVE_ACCOUNTS.setdefault(path_value, {})
    path = _active_accounts_path(path_value)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("wallets", {})


def _set_active_account(path_value: str, wallet_id: str, account_id: str) -> None:
    if path_value.startswith("mem://"):
        _ACTIVE_ACCOUNTS.setdefault(path_value, {})[wallet_id] = account_id
        return
    path = _active_accounts_path(path_value)
    payload = {"wallets": _active_account_store(path_value)}
    payload["wallets"][wallet_id] = account_id
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _get_active_account(path_value: str, wallet_id: str) -> Optional[str]:
    return _active_account_store(path_value).get(wallet_id)


def _active_accounts_path(path_value: str) -> Path:
    return Path(f"{path_value}.active.json")


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not _COLOR_READY:
        return False
    return sys.stdout.isatty()


def _colorize(text: str, tone: str, bright: bool = False) -> str:
    if not _supports_color() or Fore is None or Style is None:
        return text
    palette = {
        "blue": Fore.BLUE,
        "gold": Fore.YELLOW,
        "white": Fore.WHITE,
    }
    prefix = palette.get(tone, "")
    style = Style.BRIGHT if bright else ""
    return f"{style}{prefix}{text}{Style.RESET_ALL}"


def _print_header(title: str) -> None:
    print(_colorize(title, "blue", bright=True))
    print(_colorize("-" * len(title), "blue"))


def _print_key_value(label: str, value: str) -> None:
    print(f"{_colorize(label + ':', 'white', bright=True)} {_colorize(value, 'gold')}")


def _print_warning(message: str) -> None:
    print(_colorize(message, "gold", bright=True))


def _print_list(label: str, items: List[str]) -> None:
    print(_colorize(label + ":", "white", bright=True))
    for item in items:
        print(f"  - {_colorize(item, 'gold')}")


def _print_copy_block(label: str, value: str) -> None:
    print(_colorize(label + ":", "white", bright=True))
    print(_colorize(value, "gold"))
    if _copy_to_clipboard(value):
        print(_colorize("Copied to clipboard.", "blue"))
    else:
        print("[ready to copy]")


def _copy_to_clipboard(value: str) -> bool:
    try:
        import pyperclip  # type: ignore
    except Exception:
        pyperclip = None

    if pyperclip:
        try:
            pyperclip.copy(value)
            return True
        except Exception:
            return False

    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return _run_clip_command(["pbcopy"], value)
    if sys.platform.startswith("win") and shutil.which("clip"):
        return _run_clip_command(["clip"], value)
    for command in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            return _run_clip_command(command, value)
    return False


def _run_clip_command(command: List[str], value: str) -> bool:
    try:
        subprocess.run(command, input=value, text=True, check=False)
        return True
    except Exception:
        return False


def _dashboard(input_fn: Callable[[str], str] = input) -> int:
    options = [
        ("Wallet", _dashboard_wallet),
        ("Accounts", _dashboard_accounts),
        ("Capital", _dashboard_capital),
        ("Plans", _dashboard_plans),
        ("Simulate", _dashboard_simulate),
        ("Execute", _dashboard_execute),
        ("Backup / Export", _dashboard_backup),
        ("Exit", None),
    ]
    while True:
        _print_header("Capital OS")
        for idx, (label, _) in enumerate(options, start=1):
            print(f"{idx}. {label}")
        choice = input_fn("Select option: ").strip()
        if not choice:
            continue
        if not choice.isdigit():
            print("Invalid selection.")
            continue
        selection = int(choice)
        if selection < 1 or selection > len(options):
            print("Invalid selection.")
            continue
        label, handler = options[selection - 1]
        if handler is None:
            return 0
        handler(input_fn)


def _dashboard_wallet(input_fn: Callable[[str], str]) -> None:
    _print_header("Wallet")
    print("1. Init")
    print("2. Unlock")
    print("3. Lock")
    print("4. Show")
    print("5. Address")
    print("6. Seed")
    print("7. Backup")
    print("8. Back")
    choice = input_fn("Select option: ").strip()
    if choice == "1":
        main(
            [
                "wallet",
                "init",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--label",
                _prompt(input_fn, "Label"),
                "--passphrase",
                _prompt(input_fn, "Passphrase"),
            ]
        )
    elif choice == "2":
        main(
            [
                "wallet",
                "unlock",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
                "--passphrase",
                _prompt(input_fn, "Passphrase"),
            ]
        )
    elif choice == "3":
        main(
            [
                "wallet",
                "lock",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
            ]
        )
    elif choice == "4":
        args = [
            "wallet",
            "show",
            "--keystore",
            _prompt(input_fn, "Keystore path"),
            "--wallet-id",
            _prompt(input_fn, "Wallet ID"),
        ]
        passphrase = _prompt_optional(input_fn, "Passphrase (optional)")
        if passphrase:
            args.extend(["--passphrase", passphrase])
        main(args)
    elif choice == "5":
        args = [
            "wallet",
            "address",
            "--keystore",
            _prompt(input_fn, "Keystore path"),
            "--wallet-id",
            _prompt(input_fn, "Wallet ID"),
            "--passphrase",
            _prompt(input_fn, "Passphrase"),
        ]
        main(args)
    elif choice == "6":
        main(
            [
                "wallet",
                "seed",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
            ]
        )
    elif choice == "7":
        main(
            [
                "wallet",
                "backup",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
            ]
        )


def _dashboard_accounts(input_fn: Callable[[str], str]) -> None:
    _print_header("Accounts")
    print("1. List")
    print("2. New")
    print("3. Switch")
    print("4. Rename")
    print("5. Back")
    choice = input_fn("Select option: ").strip()
    if choice == "1":
        main(
            [
                "wallet",
                "accounts",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
            ]
        )
    elif choice == "2":
        args = [
            "account",
            "new",
            "--keystore",
            _prompt(input_fn, "Keystore path"),
            "--wallet-id",
            _prompt(input_fn, "Wallet ID"),
            "--label",
            _prompt(input_fn, "Label"),
        ]
        path = _prompt_optional(input_fn, "Derivation path (optional)")
        if path:
            args.extend(["--derivation-path", path])
        main(args)
    elif choice == "3":
        main(
            [
                "account",
                "switch",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
                "--account-id",
                _prompt(input_fn, "Account ID"),
            ]
        )
    elif choice == "4":
        main(
            [
                "account",
                "rename",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
                "--account-id",
                _prompt(input_fn, "Account ID"),
                "--label",
                _prompt(input_fn, "New label"),
            ]
        )


def _dashboard_capital(input_fn: Callable[[str], str]) -> None:
    _print_header("Capital Snapshot")
    exposures = _prompt_optional(input_fn, "Exposures (e.g. ETH=1,USDC=100)")
    if not exposures:
        main(
            [
                "status",
                "--keystore",
                _prompt(input_fn, "Keystore path"),
                "--wallet-id",
                _prompt(input_fn, "Wallet ID"),
            ]
        )
        return
    args = [
        "status",
        "--keystore",
        _prompt(input_fn, "Keystore path"),
        "--wallet-id",
        _prompt(input_fn, "Wallet ID"),
        "--snapshot-id",
        _prompt(input_fn, "Snapshot ID"),
    ]
    for entry in _split_exposures(exposures):
        args.extend(["--exposure", entry])
    passphrase = _prompt_optional(input_fn, "Passphrase (optional)")
    if passphrase:
        args.extend(["--passphrase", passphrase])
    main(args)


def _dashboard_plans(input_fn: Callable[[str], str]) -> None:
    _print_header("Create Plan")
    args = [
        "plan",
        "create",
        "--action",
        _prompt(input_fn, "Action (SWAP/TRANSFER/HOLD)"),
        "--from-asset",
        _prompt(input_fn, "From asset"),
        "--to-asset",
        _prompt(input_fn, "To asset"),
        "--amount",
        _prompt(input_fn, "Amount"),
        "--snapshot-id",
        _prompt(input_fn, "Snapshot ID"),
    ]
    exposures = _prompt(input_fn, "Exposures (e.g. ETH=1,USDC=100)")
    for entry in _split_exposures(exposures):
        args.extend(["--exposure", entry])
    main(args)


def _dashboard_simulate(input_fn: Callable[[str], str]) -> None:
    _print_header("Simulate Plan")
    main(
        [
            "simulate",
            "--plan",
            _prompt(input_fn, "Plan file path"),
        ]
    )


def _dashboard_execute(input_fn: Callable[[str], str]) -> None:
    _print_header("Execute Plan")
    mode = _prompt(input_fn, "Mode (manual/guarded)")
    args = [
        "execute",
        "--plan",
        _prompt(input_fn, "Plan file path"),
        "--mode",
        mode,
        "--arm",
    ]
    if mode == "guarded":
        actions = _prompt(input_fn, "Allowed actions (comma-separated)")
        for action in [item.strip() for item in actions.split(",") if item.strip()]:
            args.extend(["--allowed-action", action])
        assets = _prompt_optional(input_fn, "Allowed assets (comma-separated)")
        if assets:
            for asset in [item.strip() for item in assets.split(",") if item.strip()]:
                args.extend(["--allowed-asset", asset])
    if _prompt_optional(input_fn, "Confirm execution now? (y/N)") in {"y", "Y"}:
        args.append("--yes")
    main(args)


def _dashboard_backup(input_fn: Callable[[str], str]) -> None:
    _print_header("Backup / Export")
    main(
        [
            "wallet",
            "backup",
            "--keystore",
            _prompt(input_fn, "Keystore path"),
            "--wallet-id",
            _prompt(input_fn, "Wallet ID"),
        ]
    )


def _prompt(input_fn: Callable[[str], str], label: str) -> str:
    value = input_fn(f"{label}: ").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def _prompt_optional(input_fn: Callable[[str], str], label: str) -> str:
    return input_fn(f"{label}: ").strip()


def _split_exposures(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _print_status(output: dict) -> None:
    _print_header("Capital OS Status")
    _print_key_value("Wallet", output["wallet_state"])
    _print_key_value("Active Account", output["active_account"])
    if output["active_address"] != "LOCKED":
        _print_copy_block("Active Address", output["active_address"])
    else:
        _print_key_value("Active Address", output["active_address"])
    _print_key_value("Execution Mode", output["execution_mode"])
    if output["capital_total"] is not None:
        _print_key_value("Capital Total", str(output["capital_total"]))
    if output["exposures"]:
        exposure_items = [
            f"{item['asset_code']}: {item['quantity']}" for item in output["exposures"]
        ]
        _print_list("Exposures", exposure_items)
    if output["last_plan"]:
        plan = output["last_plan"]
        summary = (
            f"{plan['action_type']} {plan['amount']} "
            f"{plan['from_asset']}->{plan['to_asset']} "
            f"(steps: {plan['steps']})"
        )
        _print_key_value("Last Plan", summary)
    if output["last_dry_run"]:
        dry_run = output["last_dry_run"]
        summary = (
            f"success={dry_run['success']} "
            f"gas={dry_run['total_gas_used']} "
            f"cost={dry_run['total_cost_wei']}"
        )
        _print_key_value("Last Dry Run", summary)


if __name__ == "__main__":
    raise SystemExit(main())
