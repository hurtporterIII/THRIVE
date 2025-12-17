"""Operator CLI for the Capital OS."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

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
from wallet_core.signer import PassphraseEncryptor, WalletCore


def main(argv: Optional[List[str]] = None) -> int:
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
    wallet_accounts.set_defaults(func=_wallet_accounts)

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
    simulate_parser.set_defaults(func=_simulate_plan)

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
    keystore = FileKeyStore(Path(args.keystore))
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    metadata = wallet.create_wallet(label=args.label, passphrase=args.passphrase)
    print(metadata.wallet_id)
    return 0


def _wallet_unlock(args: argparse.Namespace) -> int:
    keystore = FileKeyStore(Path(args.keystore))
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    status = wallet.unlock(wallet_id=args.wallet_id, passphrase=args.passphrase)
    print(f"{status.wallet_id} unlocked")
    return 0


def _wallet_lock(args: argparse.Namespace) -> int:
    keystore = FileKeyStore(Path(args.keystore))
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    wallet.lock()
    print("locked")
    return 0


def _wallet_accounts(args: argparse.Namespace) -> int:
    keystore = FileKeyStore(Path(args.keystore))
    encryptor = PassphraseEncryptor()
    wallet = WalletCore(keystore=keystore, encryptor=encryptor)
    accounts = wallet.list_accounts(args.wallet_id)
    for account in accounts:
        print(f"{account.account_id} {account.label} {account.derivation_path}")
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
    output = {
        "payloads": [asdict(payload) for payload in payloads],
        "dry_run": asdict(dry_run),
    }
    print(json.dumps(output, indent=2))
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


if __name__ == "__main__":
    raise SystemExit(main())
