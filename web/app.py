"""Local-first FastAPI shell for the Capital OS and Thrive Truth Engine."""

from __future__ import annotations

from dataclasses import asdict
import html
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.engine import calculate_true_wealth
from core.models import PositionInput
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
from execution_engine.planner import ExecutionPlanner, PlanValidationError, UnimplementedIntentError, validate_plan
from wallet_core.keystore import FileKeyStore
from wallet_core.models import Account
from wallet_core.signer import PassphraseEncryptor, WalletCore

app = FastAPI(title="Capital OS", description="Local-first web shell")

_CONTEXT: Dict[str, Optional[str]] = {"keystore_path": None, "wallet_id": None}
_WALLETS: Dict[str, WalletCore] = {}
_CONTROLLER = ExecutionController()
_LAST_PLAN: Optional[ExecutionPlan] = None
_LAST_DRY_RUN: Optional[dict] = None


class ContextRequest(BaseModel):
    keystore_path: str
    wallet_id: str


class UnlockRequest(BaseModel):
    passphrase: str


class SeedExportRequest(BaseModel):
    passphrase: str
    acknowledge_warning: bool


class AccountSelectRequest(BaseModel):
    account_id: str


class ExposureInput(BaseModel):
    asset_code: str
    quantity: float


class IntentInput(BaseModel):
    action_type: str
    from_asset: str
    to_asset: str
    amount: float
    notes: Optional[List[str]] = None


class PlanRequest(BaseModel):
    snapshot_id: str
    exposures: List[ExposureInput]
    intent: IntentInput
    notes: Optional[List[str]] = None


class PlanPayload(BaseModel):
    plan: dict


class ExecutionModeRequest(BaseModel):
    mode: str
    allowed_action_types: Optional[List[str]] = None
    allowed_assets: Optional[List[str]] = None


class ExecutionArmRequest(BaseModel):
    armed: bool


class ExecuteRequest(BaseModel):
    plan: dict
    confirm_all: bool


@app.middleware("http")
async def _local_only(request: Request, call_next):
    client = request.client
    if client is not None:
        host = client.host
        if host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"error": "Remote access disabled."}, status_code=403)
    return await call_next(request)


async def _handle_errors(request: Request, exc: Exception):
    return JSONResponse({"error": str(exc)}, status_code=400)


for _exc_class in (
    AdapterError,
    ExecutionBlockedError,
    ModeTransitionError,
    PlanValidationError,
    PolicyViolationError,
    SimulationError,
    UnimplementedIntentError,
    ValueError,
    KeyError,
):
    app.add_exception_handler(_exc_class, _handle_errors)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_render_dashboard())


@app.get("/truth-engine", response_class=HTMLResponse)
async def truth_engine_form() -> HTMLResponse:
    return HTMLResponse(_render_truth_engine_form())


@app.post("/calculate", response_class=HTMLResponse)
async def calculate(
    asset_type: str = Form(...),
    quantity: float = Form(...),
    cost_basis_per_unit: float = Form(...),
    current_price: float = Form(...),
    days_held: int = Form(...),
    state_tax_rate: Optional[str] = Form(None),
) -> HTMLResponse:
    state_rate_value: Optional[float] = None
    if state_tax_rate not in (None, ""):
        try:
            state_rate_value = float(state_tax_rate)
        except ValueError:
            state_rate_value = None

    position = PositionInput(
        asset_type=asset_type,
        ticker="TICKER",
        quantity=float(quantity),
        cost_basis_per_unit=float(cost_basis_per_unit),
        current_price=float(current_price),
        days_held=int(days_held),
        state_tax_rate=state_rate_value,
    )

    result = calculate_true_wealth(position)
    result_data = result.to_dict()

    def format_value(value: object) -> str:
        if isinstance(value, list):
            items = "".join(f"<li>{html.escape(str(item))}</li>" for item in value)
            return f"<ul>{items}</ul>"
        return html.escape(str(value))

    summary_items = [
        f"<li><strong>{html.escape(str(key))}</strong>: {format_value(value)}</li>"
        for key, value in result_data.items()
    ]

    net_liquid = result_data.get("net_liquid_wealth")
    net_liquid_display = (
        f"${net_liquid:,.2f}" if isinstance(net_liquid, (int, float)) else "N/A"
    )

    raw_json = html.escape(json.dumps(result_data, indent=2))

    result_section = f"""
  <div>
    <h2>After-Tax Liquid Wealth</h2>
    <p>Net liquid wealth: {net_liquid_display}</p>
    <h3>Details</h3>
    <ul>{''.join(summary_items)}</ul>
    <h3>Raw JSON</h3>
    <pre>{raw_json}</pre>
  </div>
  <p><a href="/truth-engine">Back to form</a></p>
"""

    return HTMLResponse(_render_truth_engine_form(result_section))


@app.post("/api/context")
async def set_context(payload: ContextRequest):
    _ensure_wallet_exists(payload.keystore_path, payload.wallet_id)
    _CONTEXT["keystore_path"] = payload.keystore_path
    _CONTEXT["wallet_id"] = payload.wallet_id
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    keystore_path, wallet_id = _require_context()
    wallet = _get_wallet(keystore_path)
    _ensure_wallet_exists(keystore_path, wallet_id)

    wallet_state = "UNLOCKED" if wallet.status(wallet_id).unlocked else "LOCKED"
    account = _select_account(wallet, keystore_path, wallet_id)
    active_address = "LOCKED"
    if wallet_state == "UNLOCKED" and account is not None:
        active_address = wallet.get_public_key(wallet_id, account.derivation_path)

    return {
        "wallet_state": wallet_state,
        "active_account": account.label if account else None,
        "active_account_id": account.account_id if account else None,
        "active_address": active_address,
        "execution_mode": _CONTROLLER.mode.value,
        "armed": _CONTROLLER.armed,
    }


@app.post("/api/wallet/unlock")
async def wallet_unlock(payload: UnlockRequest):
    keystore_path, wallet_id = _require_context()
    if not payload.passphrase:
        raise HTTPException(status_code=400, detail="Passphrase required.")
    wallet = _get_wallet(keystore_path)
    try:
        wallet.unlock(wallet_id=wallet_id, passphrase=payload.passphrase)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"wallet_state": "UNLOCKED"}


@app.post("/api/wallet/lock")
async def wallet_lock():
    keystore_path, _ = _require_context()
    wallet = _get_wallet(keystore_path)
    wallet.lock()
    return {"wallet_state": "LOCKED"}


@app.post("/api/wallet/seed")
async def wallet_seed(payload: SeedExportRequest):
    if not payload.acknowledge_warning:
        raise HTTPException(status_code=400, detail="Warning acknowledgement required.")
    keystore_path, wallet_id = _require_context()
    wallet = _get_wallet(keystore_path)
    was_unlocked = wallet.status(wallet_id).unlocked
    if not payload.passphrase:
        raise HTTPException(status_code=400, detail="Passphrase required.")
    wallet.unlock(wallet_id=wallet_id, passphrase=payload.passphrase)
    phrase = wallet.export_recovery_phrase(wallet_id)
    if not was_unlocked:
        wallet.lock()
    return {
        "wallet_id": wallet_id,
        "seed_phrase": phrase,
        "warning": "Anyone with this phrase controls your funds.",
    }


@app.get("/api/accounts")
async def list_accounts():
    keystore_path, wallet_id = _require_context()
    wallet = _get_wallet(keystore_path)
    accounts = wallet.list_accounts(wallet_id)
    active_id = _get_active_account(keystore_path, wallet_id)
    return {
        "accounts": [
            {
                "account_id": account.account_id,
                "label": account.label,
                "active": account.account_id == active_id,
            }
            for account in accounts
        ]
    }


@app.post("/api/accounts/select")
async def select_account(payload: AccountSelectRequest):
    keystore_path, wallet_id = _require_context()
    wallet = _get_wallet(keystore_path)
    accounts = wallet.list_accounts(wallet_id)
    if not any(account.account_id == payload.account_id for account in accounts):
        raise HTTPException(status_code=404, detail="Account not found.")
    _set_active_account(keystore_path, wallet_id, payload.account_id)
    return {"status": "ok"}


@app.post("/api/plans")
async def create_plan(payload: PlanRequest):
    intent = _build_intent(payload.intent)
    capital_state = _build_capital_state(payload.snapshot_id, payload.exposures, payload.notes)
    planner = ExecutionPlanner()
    plan = planner.plan(intent, capital_state)
    global _LAST_PLAN
    _LAST_PLAN = plan
    return _plan_to_dict(plan)


@app.post("/api/simulate")
async def simulate_plan(payload: PlanPayload):
    plan = _plan_from_dict(payload.plan)
    payloads = plan_to_payloads(plan)
    dry_run = simulate(payloads)
    global _LAST_DRY_RUN
    _LAST_DRY_RUN = asdict(dry_run)
    return {
        "payloads": [asdict(item) for item in payloads],
        "dry_run": asdict(dry_run),
    }


@app.post("/api/execution/mode")
async def set_execution_mode(payload: ExecutionModeRequest):
    mode = _parse_mode(payload.mode)
    policy = None
    if mode == ExecutionMode.GUARDED:
        if not payload.allowed_action_types:
            raise HTTPException(status_code=400, detail="Guarded mode requires allowed actions.")
        actions = tuple(_parse_action(action) for action in payload.allowed_action_types)
        assets = tuple(payload.allowed_assets) if payload.allowed_assets else None
        policy = GuardPolicy(allowed_action_types=actions, allowed_assets=assets)
    _CONTROLLER.set_mode(mode, policy=policy)
    return {"mode": _CONTROLLER.mode.value}


@app.post("/api/execution/arm")
async def set_execution_arm(payload: ExecutionArmRequest):
    if payload.armed:
        _CONTROLLER.arm()
    else:
        _CONTROLLER.disarm()
    return {"armed": _CONTROLLER.armed}


@app.post("/api/execute")
async def execute_plan(payload: ExecuteRequest):
    plan = _plan_from_dict(payload.plan)
    if not payload.confirm_all:
        raise HTTPException(status_code=400, detail="Explicit confirmation required.")

    def confirm_step(step: ExecutionStep) -> bool:
        return True

    decisions = _CONTROLLER.evaluate_plan(plan, confirm_step=confirm_step)
    return {
        "mode": _CONTROLLER.mode.value,
        "decisions": [asdict(decision) for decision in decisions],
    }


def _render_truth_engine_form(result_section: str = "") -> str:
    description = (
        "Thrive reveals after-tax, real-world net wealth using the Truth Engine. "
        "Submit a position to see the liquidation reality."
    )
    return f"""<!DOCTYPE html>
<html data-theme=\"day\">
<head>
  <meta charset=\"UTF-8\" />
  <title>Thrive Truth Engine</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f8fb;
      --text: #0b0d12;
      --panel: #ffffff;
      --border: #d3d8e0;
      --blue: #1f5fbf;
      --gold: #c28a10;
      --muted: #596273;
      --input-bg: #ffffff;
      --input-border: #b8c0cc;
    }}
    [data-theme=\"night\"] {{
      --bg: #0b1118;
      --text: #f1f4f8;
      --panel: #121a24;
      --border: #2a3340;
      --blue: #5aa2ff;
      --gold: #f3b43f;
      --muted: #a5b0c2;
      --input-bg: #0f1722;
      --input-border: #2a3340;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: \"Segoe UI\", \"Helvetica Neue\", Arial, sans-serif;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      padding: 1.5rem 2rem 0.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    main {{
      padding: 0 2rem 2rem;
      max-width: 900px;
      margin: 0 auto;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
    }}
    h1 {{ margin: 0; font-size: 1.8rem; }}
    h2 {{ margin-top: 0; }}
    label {{ display: block; margin: 0.75rem 0 0.25rem; font-weight: 600; }}
    input, textarea {{
      width: 100%;
      padding: 0.6rem 0.7rem;
      border-radius: 8px;
      border: 1px solid var(--input-border);
      background: var(--input-bg);
      color: var(--text);
    }}
    button {{
      border: none;
      border-radius: 8px;
      padding: 0.6rem 1.2rem;
      font-weight: 600;
      cursor: pointer;
      background: var(--blue);
      color: white;
      margin-top: 0.75rem;
    }}
    button:hover {{ opacity: 0.92; }}
    a {{ color: var(--blue); }}
    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 600;
    }}
    .toggle input {{ width: auto; }}
    .muted {{ color: var(--muted); }}
    pre {{
      background: rgba(0, 0, 0, 0.04);
      padding: 1rem;
      border-radius: 8px;
      overflow-x: auto;
    }}
    .noscript {{ color: var(--gold); font-weight: 600; }}
  </style>
</head>
<body data-theme="day">
  <header>
    <div>
      <h1>Thrive Truth Engine</h1>
      <div class=\"muted\">After-tax reality for a single position.</div>
    </div>
    <label class=\"toggle\">
      Night mode
      <input id=\"themeToggle\" type=\"checkbox\" aria-label=\"Toggle night mode\" />
    </label>
  </header>
  <main>
    <noscript><p class=\"noscript\">JavaScript is off. Theme toggle and live actions are unavailable.</p></noscript>
    <section class=\"panel\">
      <p>{html.escape(description)}</p>
      <form action=\"/calculate\" method=\"post\">
        <label>Asset type</label>
        <input name=\"asset_type\" value=\"stock\" required />
        <label>Quantity</label>
        <input type=\"number\" step=\"any\" name=\"quantity\" required />
        <label>Cost basis per unit</label>
        <input type=\"number\" step=\"any\" name=\"cost_basis_per_unit\" required />
        <label>Current price</label>
        <input type=\"number\" step=\"any\" name=\"current_price\" required />
        <label>Days held</label>
        <input type=\"number\" name=\"days_held\" required />
        <label>State tax rate (optional)</label>
        <input type=\"number\" step=\"any\" name=\"state_tax_rate\" />
        <button type=\"submit\">Calculate</button>
      </form>
    </section>
    {result_section}
    <p><a href=\"/\">Back to Capital OS</a></p>
  </main>
  <script>
    (function() {{
      var key = 'capitalos-theme';
      var root = document.documentElement;
      var body = document.body || root;
      var toggle = document.getElementById('themeToggle');
      function apply(theme) {{
        root.setAttribute('data-theme', theme);
        if (body) body.setAttribute('data-theme', theme);
        if (toggle) toggle.checked = theme === 'night';
      }}
      var theme = 'day';
      try {{
        var stored = localStorage.getItem(key);
        if (stored) theme = stored;
      }} catch (err) {{}}
      apply(theme);
      if (toggle) {{
        toggle.onchange = function() {{
          var next = toggle.checked ? 'night' : 'day';
          apply(next);
          try {{ localStorage.setItem(key, next); }} catch (err) {{}}
        }};
      }}
    }})();
  </script>
</body>
</html>"""


def _render_dashboard() -> str:
    return """<!DOCTYPE html>
<html data-theme="day">
<head>
  <meta charset="UTF-8" />
  <title>Capital OS</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f8fb;
      --text: #0b0d12;
      --panel: #ffffff;
      --border: #d3d8e0;
      --blue: #1f5fbf;
      --gold: #c28a10;
      --muted: #596273;
      --input-bg: #ffffff;
      --input-border: #b8c0cc;
      --danger-bg: rgba(194, 138, 16, 0.1);
      --success: #2b7a37;
      --success-bg: rgba(43, 122, 55, 0.12);
      --muted-bg: rgba(89, 98, 115, 0.08);
    }
    [data-theme="night"] {
      --bg: #0b1118;
      --text: #f1f4f8;
      --panel: #121a24;
      --border: #2a3340;
      --blue: #5aa2ff;
      --gold: #f3b43f;
      --muted: #a5b0c2;
      --input-bg: #0f1722;
      --input-border: #2a3340;
      --danger-bg: rgba(243, 180, 63, 0.15);
      --success: #8ce3a1;
      --success-bg: rgba(140, 227, 161, 0.12);
      --muted-bg: rgba(165, 176, 194, 0.1);
    }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    header {
      padding: 1.5rem 2rem 0.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }
    header h1 { margin: 0; font-size: 2rem; }
    header p { margin: 0.25rem 0 0; color: var(--muted); }
    main { padding: 0 2rem 2.5rem; }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }
    h2 { margin-top: 0; font-size: 1.3rem; }
    label { display: block; margin: 0.6rem 0 0.25rem; font-weight: 600; }
    input, select, textarea {
      width: 100%;
      padding: 0.55rem 0.7rem;
      border-radius: 8px;
      border: 1px solid var(--input-border);
      background: var(--input-bg);
      color: var(--text);
    }
    button {
      border: none;
      border-radius: 8px;
      padding: 0.55rem 1.2rem;
      font-weight: 600;
      cursor: pointer;
      background: var(--blue);
      color: white;
      margin-top: 0.75rem;
      margin-right: 0.4rem;
    }
    button:hover { opacity: 0.92; }
    .btn-secondary { background: transparent; color: var(--blue); border: 1px solid var(--blue); }
    .btn-link { background: transparent; color: var(--blue); border: none; padding: 0; margin: 0; }
    .btn-warn { background: var(--gold); color: #1c1302; }
    pre { background: rgba(0, 0, 0, 0.04); padding: 0.75rem; white-space: pre-wrap; border-radius: 8px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    .warn { color: var(--gold); font-weight: 700; }
    .panel-warn { border-color: var(--gold); background: var(--danger-bg); }
    a { color: var(--blue); }
    .toggle {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 600;
    }
    .toggle input { width: auto; }
    .toolbar { display: flex; align-items: center; gap: 1rem; }
    .noscript { color: var(--gold); font-weight: 600; }
    .step {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
      background: var(--panel);
    }
    .step-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }
    .step-title { font-size: 1.1rem; font-weight: 700; margin: 0; }
    .step-status {
      font-size: 0.85rem;
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      background: var(--muted-bg);
      color: var(--muted);
    }
    .step.is-complete .step-status {
      background: var(--success-bg);
      color: var(--success);
    }
    .step.is-current { border-color: var(--blue); box-shadow: 0 0 0 2px rgba(31, 95, 191, 0.15); }
    .step-body { margin-top: 0.75rem; }
    .step.is-collapsed .step-body { display: none; }
    .field.is-hidden { display: none; }
    .help { color: var(--muted); font-size: 0.85rem; margin-top: 0.2rem; }
    .phase-title {
      font-size: 0.9rem;
      letter-spacing: 0.12rem;
      color: var(--muted);
      text-transform: uppercase;
      font-weight: 700;
      margin-bottom: 0.75rem;
    }
    .phase-grid { display: grid; gap: 1rem; }
    .phase-card { padding: 1rem; border-radius: 12px; border: 1px solid var(--border); background: var(--panel); }
    .advanced-fields { margin-top: 0.75rem; }
    .js .advanced-fields { display: none; }
    .js .show-advanced .advanced-fields { display: block; }
    .execution-muted { opacity: 0.65; filter: grayscale(0.25); }
    .execution-muted .btn-warn { opacity: 0.7; }
    @media (max-width: 860px) {
      .row { grid-template-columns: 1fr; }
      header { flex-direction: column; align-items: flex-start; }
      .toolbar { width: 100%; justify-content: space-between; }
    }
  </style>
</head>
<body data-theme="day">
  <header>
    <div>
      <h1>Capital OS Web Shell</h1>
      <p>Guided operator flow for local planning, simulation, and execution gating.</p>
    </div>
    <div class="toolbar">
      <label class="toggle">
        Night mode
        <input id="themeToggle" type="checkbox" aria-label="Toggle night mode" />
      </label>
    </div>
  </header>
  <main>
  <noscript><p class="noscript">JavaScript is off. Live actions are unavailable in this shell.</p></noscript>

  <section>
    <h2>Startup Flow</h2>
    <div id="step-context" class="step">
      <div class="step-header">
        <div>
          <div class="step-title">Step 1: Set Context</div>
          <div class="help">Point to the keystore and wallet you want to operate.</div>
        </div>
        <div>
          <span id="contextStatus" class="step-status">Not set</span>
          <button type="button" class="btn-link" onclick="toggleStep('step-context')">Edit</button>
        </div>
      </div>
      <div class="step-body">
        <label>Keystore Path <input id="keystorePath" placeholder="/path/to/keystore.json" /></label>
        <label>Wallet ID <input id="walletId" placeholder="wallet id" /></label>
        <button onclick="setContext()">Set Context</button>
        <div id="contextResult"></div>
      </div>
    </div>

    <div id="step-unlock" class="step">
      <div class="step-header">
        <div>
          <div class="step-title">Step 2: Unlock Wallet</div>
          <div class="help">Unlock only for the duration of your review.</div>
        </div>
        <div>
          <span id="unlockStatus" class="step-status">Locked</span>
          <button type="button" class="btn-link" onclick="toggleStep('step-unlock')">Edit</button>
        </div>
      </div>
      <div class="step-body">
        <label>Passphrase <input id="unlockPassphrase" type="password" /></label>
        <button onclick="unlockWallet()">Unlock</button>
        <button class="btn-secondary" onclick="lockWallet()">Lock</button>
      </div>
    </div>

    <div id="step-account" class="step">
      <div class="step-header">
        <div>
          <div class="step-title">Step 3: Select Account</div>
          <div class="help">Choose the active account used for addresses and signatures.</div>
        </div>
        <div>
          <span id="accountStatus" class="step-status">Not selected</span>
          <button type="button" class="btn-link" onclick="toggleStep('step-account')">Edit</button>
        </div>
      </div>
      <div class="step-body">
        <button onclick="loadAccounts()">Load Accounts</button>
        <label>Account ID <input id="accountId" /></label>
        <button onclick="selectAccount()">Set Active Account</button>
        <pre id="accountsOutput"></pre>
      </div>
    </div>
  </section>

  <section>
    <h2>Status</h2>
    <button onclick="refreshStatus()">Refresh</button>
    <pre id="statusOutput">{}</pre>
  </section>

  <section class="panel-warn">
    <h2>Wallet Visibility</h2>
    <div class="row">
      <div>
        <label>Seed Export Passphrase <input id="seedPassphrase" type="password" /></label>
        <label><input id="seedAck" type="checkbox" /> I understand this exposes my funds.</label>
        <button class="btn-warn" onclick="exportSeed()">Export Seed</button>
        <div class="warn">Anyone with this phrase controls your funds.</div>
      </div>
      <div>
        <div class="help">Seed export is explicit and never cached.</div>
      </div>
    </div>
    <pre id="walletOutput"></pre>
  </section>

  <section>
    <div class="phase-title">Think</div>
    <div class="phase-grid">
      <div class="phase-card" id="planSection">
        <h2>Plan</h2>
        <label>What do you want to do?</label>
        <select id="actionType">
          <option>HOLD</option>
          <option>SWAP</option>
          <option>TRANSFER</option>
        </select>
        <div class="help">Choose the intent for this plan. The form adapts based on action.</div>

        <div class="row">
          <div class="field" data-actions="HOLD SWAP TRANSFER">
            <label><span id="fromAssetLabel">From Asset</span> <input id="fromAsset" value="USD" /></label>
            <div class="help">Asset to use as the source.</div>
          </div>
          <div class="field" data-actions="SWAP TRANSFER" id="toAssetField">
            <label>To Asset <input id="toAsset" value="USD" /></label>
            <div class="help">Destination asset for swap or transfer.</div>
          </div>
        </div>

        <div class="field" data-actions="HOLD SWAP TRANSFER">
          <label>Amount <input id="intentAmount" type="number" step="any" value="100" /></label>
          <div class="help">Quantity to plan for.</div>
        </div>

        <button type="button" class="btn-secondary" onclick="toggleAdvanced()">Advanced</button>
        <div class="advanced-fields">
          <label>Snapshot ID <input id="snapshotId" value="snapshot-1" /></label>
          <div class="help">Capital snapshot identifier.</div>
          <label>Exposures (one per line: ASSET:quantity)
            <textarea id="exposures" rows="4">USD:1000</textarea>
          </label>
          <div class="help">Define the capital snapshot used for planning.</div>
        </div>

        <button onclick="createPlan()">Create Plan</button>
        <pre id="planOutput"></pre>
      </div>

      <div class="phase-card">
        <h2>Simulate</h2>
        <div class="help">Dry-run the most recent plan before deciding.</div>
        <button onclick="simulatePlan()">Simulate Last Plan</button>
      </div>
    </div>
  </section>

  <section>
    <div class="phase-title">Review</div>
    <h2>Simulation Results</h2>
    <pre id="simulateOutput"></pre>
  </section>

  <section id="executionSection" class="panel-warn execution-muted">
    <div class="phase-title">Act</div>
    <h2>Execution</h2>
    <div class="help">Execution remains gated until mode, arm, and confirmation are all set.</div>
    <div class="row">
      <label>Mode
        <select id="execMode">
          <option value="SAFE">SAFE</option>
          <option value="MANUAL">MANUAL</option>
          <option value="GUARDED">GUARDED</option>
        </select>
      </label>
      <label>Allowed Actions (guarded, comma separated)
        <input id="allowedActions" placeholder="SWAP,TRANSFER" />
      </label>
      <label>Allowed Assets (guarded, comma separated)
        <input id="allowedAssets" placeholder="USD,ETH" />
      </label>
    </div>
    <button onclick="setMode()">Set Mode</button>
    <label><input id="armed" type="checkbox" /> Armed</label>
    <button class="btn-secondary" onclick="setArmed()">Update Arm</button>
    <label><input id="confirmAll" type="checkbox" /> I confirm execution</label>
    <button class="btn-warn" onclick="executePlan()">Execute Last Plan</button>
    <pre id="executeOutput"></pre>
  </section>

  <section>
    <h2>Truth Engine</h2>
    <p><a href="/truth-engine">Open the Thrive Truth Engine form</a></p>
  </section>
  </main>

  <script>
    (function() {
      var key = 'capitalos-theme';
      var root = document.documentElement;
      var body = document.body || root;
      var toggle = document.getElementById('themeToggle');
      function apply(theme) {
        root.setAttribute('data-theme', theme);
        if (body) body.setAttribute('data-theme', theme);
        if (toggle) toggle.checked = theme === 'night';
      }
      var theme = 'day';
      try {
        var stored = localStorage.getItem(key);
        if (stored) theme = stored;
      } catch (err) {}
      apply(theme);
      if (body) body.classList.add('js');
      if (toggle) {
        toggle.onchange = function() {
          var next = toggle.checked ? 'night' : 'day';
          apply(next);
          try { localStorage.setItem(key, next); } catch (err) {}
        };
      }
    })();
  </script>
  <script>
    let lastPlan = null;
    let contextReady = false;
    let lastStatus = null;

    function readContext() {
      return {
        keystore_path: document.getElementById('keystorePath').value,
        wallet_id: document.getElementById('walletId').value,
      };
    }

    async function apiPost(path, payload) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      return handleResponse(response);
    }

    async function apiGet(path) {
      const response = await fetch(path);
      return handleResponse(response);
    }

    async function handleResponse(response) {
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch (err) { data = { raw: text }; }
      if (!response.ok) {
        throw data;
      }
      return data;
    }

    function renderOutput(elementId, data) {
      document.getElementById(elementId).textContent = JSON.stringify(data, null, 2);
    }

    async function setContext() {
      try {
        const data = await apiPost('/api/context', readContext());
        contextReady = true;
        renderOutput('contextResult', data);
        await refreshStatus();
      } catch (err) {
        renderOutput('contextResult', err);
      }
    }

    async function refreshStatus() {
      try {
        const data = await apiGet('/api/status');
        lastStatus = data;
        renderOutput('statusOutput', data);
        updateStepDisplay(data);
      } catch (err) {
        renderOutput('statusOutput', err);
      }
    }

    async function unlockWallet() {
      try {
        const data = await apiPost('/api/wallet/unlock', {
          passphrase: document.getElementById('unlockPassphrase').value,
        });
        renderOutput('walletOutput', data);
        await refreshStatus();
      } catch (err) {
        renderOutput('walletOutput', err);
      }
    }

    async function lockWallet() {
      try {
        const data = await apiPost('/api/wallet/lock', {});
        renderOutput('walletOutput', data);
        await refreshStatus();
      } catch (err) {
        renderOutput('walletOutput', err);
      }
    }

    async function exportSeed() {
      try {
        const data = await apiPost('/api/wallet/seed', {
          passphrase: document.getElementById('seedPassphrase').value,
          acknowledge_warning: document.getElementById('seedAck').checked,
        });
        renderOutput('walletOutput', data);
      } catch (err) {
        renderOutput('walletOutput', err);
      }
    }

    async function loadAccounts() {
      try {
        const data = await apiGet('/api/accounts');
        renderOutput('accountsOutput', data);
      } catch (err) {
        renderOutput('accountsOutput', err);
      }
    }

    async function selectAccount() {
      try {
        const data = await apiPost('/api/accounts/select', {
          account_id: document.getElementById('accountId').value,
        });
        renderOutput('accountsOutput', data);
        await refreshStatus();
      } catch (err) {
        renderOutput('accountsOutput', err);
      }
    }

    function parseExposures() {
      const raw = document.getElementById('exposures').value;
      const lines = raw.split('
').map(line => line.trim()).filter(Boolean);
      return lines.map(line => {
        const parts = line.split(':');
        return { asset_code: parts[0].trim(), quantity: Number(parts[1]) };
      });
    }

    async function createPlan() {
      try {
        const payload = {
          snapshot_id: document.getElementById('snapshotId').value,
          exposures: parseExposures(),
          intent: {
            action_type: document.getElementById('actionType').value,
            from_asset: document.getElementById('fromAsset').value,
            to_asset: document.getElementById('toAsset').value,
            amount: Number(document.getElementById('intentAmount').value),
          },
        };
        const data = await apiPost('/api/plans', payload);
        lastPlan = data;
        renderOutput('planOutput', data);
      } catch (err) {
        renderOutput('planOutput', err);
      }
    }

    async function simulatePlan() {
      if (!lastPlan) {
        renderOutput('simulateOutput', { error: 'Create a plan first.' });
        return;
      }
      try {
        const data = await apiPost('/api/simulate', { plan: lastPlan });
        renderOutput('simulateOutput', data);
      } catch (err) {
        renderOutput('simulateOutput', err);
      }
    }

    async function setMode() {
      try {
        const allowedActions = document.getElementById('allowedActions').value;
        const allowedAssets = document.getElementById('allowedAssets').value;
        const data = await apiPost('/api/execution/mode', {
          mode: document.getElementById('execMode').value,
          allowed_action_types: allowedActions ? allowedActions.split(',').map(item => item.trim()).filter(Boolean) : null,
          allowed_assets: allowedAssets ? allowedAssets.split(',').map(item => item.trim()).filter(Boolean) : null,
        });
        renderOutput('executeOutput', data);
        updateExecutionState();
        await refreshStatus();
      } catch (err) {
        renderOutput('executeOutput', err);
      }
    }

    async function setArmed() {
      try {
        const data = await apiPost('/api/execution/arm', {
          armed: document.getElementById('armed').checked,
        });
        renderOutput('executeOutput', data);
        updateExecutionState();
        await refreshStatus();
      } catch (err) {
        renderOutput('executeOutput', err);
      }
    }

    async function executePlan() {
      if (!lastPlan) {
        renderOutput('executeOutput', { error: 'Create a plan first.' });
        return;
      }
      try {
        const data = await apiPost('/api/execute', {
          plan: lastPlan,
          confirm_all: document.getElementById('confirmAll').checked,
        });
        renderOutput('executeOutput', data);
      } catch (err) {
        renderOutput('executeOutput', err);
      }
    }

    function toggleStep(stepId) {
      const step = document.getElementById(stepId);
      if (!step) return;
      const collapsed = step.classList.contains('is-collapsed');
      if (collapsed) {
        step.classList.remove('is-collapsed');
        step.dataset.userOpen = 'true';
      } else {
        step.classList.add('is-collapsed');
        step.dataset.userOpen = 'false';
      }
    }

    function updateStepDisplay(status) {
      const contextDone = contextReady;
      const unlocked = status && status.wallet_state === 'UNLOCKED';
      const accountDone = status && status.active_account_id;
      setStepState('step-context', contextDone, contextDone ? 'Complete ✓' : 'Not set');
      setStepState('step-unlock', unlocked, unlocked ? 'Unlocked ✓' : 'Locked');
      setStepState('step-account', !!accountDone, accountDone ? 'Selected ✓' : 'Not selected');
      setCurrentStep([contextDone, unlocked, !!accountDone]);
    }

    function setStepState(stepId, complete, label) {
      const step = document.getElementById(stepId);
      const status = document.getElementById(stepId === 'step-context' ? 'contextStatus'
        : stepId === 'step-unlock' ? 'unlockStatus'
        : 'accountStatus');
      if (!step || !status) return;
      status.textContent = label;
      if (complete) {
        step.classList.add('is-complete');
        if (step.dataset.userOpen !== 'true') {
          step.classList.add('is-collapsed');
        }
      } else {
        step.classList.remove('is-complete');
        step.classList.remove('is-collapsed');
      }
    }

    function setCurrentStep(completions) {
      const steps = ['step-context', 'step-unlock', 'step-account'];
      steps.forEach(id => {
        const step = document.getElementById(id);
        if (step) step.classList.remove('is-current');
      });
      for (let index = 0; index < steps.length; index += 1) {
        if (!completions[index]) {
          const step = document.getElementById(steps[index]);
          if (step) step.classList.add('is-current');
          break;
        }
      }
    }

    function toggleAdvanced() {
      const planSection = document.getElementById('planSection');
      if (!planSection) return;
      planSection.classList.toggle('show-advanced');
    }

    function updatePlanFields() {
      const action = document.getElementById('actionType').value;
      const fields = document.querySelectorAll('#planSection .field');
      fields.forEach(field => {
        const actions = (field.getAttribute('data-actions') || '').split(' ');
        if (actions.includes(action)) {
          field.classList.remove('is-hidden');
        } else {
          field.classList.add('is-hidden');
        }
      });
      const fromLabel = document.getElementById('fromAssetLabel');
      const fromAsset = document.getElementById('fromAsset');
      const toAsset = document.getElementById('toAsset');
      if (action === 'HOLD') {
        if (fromLabel) fromLabel.textContent = 'Asset';
        if (toAsset && fromAsset) toAsset.value = fromAsset.value;
      } else {
        if (fromLabel) fromLabel.textContent = 'From Asset';
      }
    }

    function updateExecutionState() {
      const mode = document.getElementById('execMode').value;
      const armed = document.getElementById('armed').checked;
      const confirmed = document.getElementById('confirmAll').checked;
      const ready = mode !== 'SAFE' && armed && confirmed;
      const section = document.getElementById('executionSection');
      if (section) {
        if (ready) {
          section.classList.remove('execution-muted');
        } else {
          section.classList.add('execution-muted');
        }
      }
    }

    function bindUI() {
      const actionType = document.getElementById('actionType');
      const confirmAll = document.getElementById('confirmAll');
      const execMode = document.getElementById('execMode');
      const armed = document.getElementById('armed');
      if (actionType) actionType.addEventListener('change', updatePlanFields);
      if (confirmAll) confirmAll.addEventListener('change', updateExecutionState);
      if (execMode) execMode.addEventListener('change', updateExecutionState);
      if (armed) armed.addEventListener('change', updateExecutionState);
      updatePlanFields();
      updateExecutionState();
    }

    bindUI();
  </script>
</body>
</html>"""


def _require_context() -> Tuple[str, str]:
    keystore_path = _CONTEXT.get("keystore_path")
    wallet_id = _CONTEXT.get("wallet_id")
    if not keystore_path or not wallet_id:
        raise HTTPException(status_code=400, detail="Context not set.")
    return keystore_path, wallet_id


def _ensure_wallet_exists(keystore_path: str, wallet_id: str) -> None:
    keystore = FileKeyStore(Path(keystore_path))
    keystore.load(wallet_id)


def _get_wallet(keystore_path: str) -> WalletCore:
    key = str(Path(keystore_path))
    wallet = _WALLETS.get(key)
    if wallet is None:
        wallet = WalletCore(
            keystore=FileKeyStore(Path(keystore_path)),
            encryptor=PassphraseEncryptor(),
        )
        _WALLETS[key] = wallet
    return wallet


def _active_accounts_path(keystore_path: str) -> Path:
    return Path(f"{keystore_path}.active.json")


def _active_account_store(keystore_path: str) -> dict:
    path = _active_accounts_path(keystore_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("wallets", {})


def _set_active_account(keystore_path: str, wallet_id: str, account_id: str) -> None:
    path = _active_accounts_path(keystore_path)
    payload = {"wallets": _active_account_store(keystore_path)}
    payload["wallets"][wallet_id] = account_id
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _get_active_account(keystore_path: str, wallet_id: str) -> Optional[str]:
    return _active_account_store(keystore_path).get(wallet_id)


def _select_account(
    wallet: WalletCore, keystore_path: str, wallet_id: str
) -> Optional[Account]:
    accounts = wallet.list_accounts(wallet_id)
    active_id = _get_active_account(keystore_path, wallet_id)
    if active_id:
        for account in accounts:
            if account.account_id == active_id:
                return account
    return accounts[0] if accounts else None


def _parse_action(value: str) -> ActionType:
    action = ActionType.__members__.get(value.upper())
    if action is None:
        raise ValueError(f"Unsupported action type: {value}")
    return action


def _parse_mode(value: str) -> ExecutionMode:
    mode = ExecutionMode.__members__.get(value.upper())
    if mode is None:
        raise ValueError(f"Unsupported mode: {value}")
    return mode


def _build_intent(intent: IntentInput) -> ExecutionIntent:
    return ExecutionIntent(
        action_type=_parse_action(intent.action_type),
        from_asset=intent.from_asset,
        to_asset=intent.to_asset,
        amount=float(intent.amount),
        notes=tuple(intent.notes or ()),
    )


def _build_capital_state(
    snapshot_id: str,
    exposures: Iterable[ExposureInput],
    notes: Optional[List[str]],
) -> CapitalState:
    return CapitalState(
        snapshot_id=snapshot_id,
        exposures=tuple(
            CapitalExposure(asset_code=item.asset_code, quantity=float(item.quantity))
            for item in exposures
        ),
        notes=tuple(notes or ()),
    )


def _plan_to_dict(plan: ExecutionPlan) -> dict:
    return {
        "intent": {
            "action_type": plan.intent.action_type.value,
            "from_asset": plan.intent.from_asset,
            "to_asset": plan.intent.to_asset,
            "amount": plan.intent.amount,
            "notes": list(plan.intent.notes),
        },
        "capital_state": {
            "snapshot_id": plan.capital_state.snapshot_id,
            "exposures": [
                {"asset_code": exposure.asset_code, "quantity": exposure.quantity}
                for exposure in plan.capital_state.exposures
            ],
            "notes": list(plan.capital_state.notes),
        },
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
    plan = ExecutionPlan(
        intent=intent,
        capital_state=capital_state,
        steps=steps,
        assumptions=tuple(data["assumptions"]),
        failure_modes=tuple(data["failure_modes"]),
        required_signatures=required_signatures,
        estimated_cost=float(data["estimated_cost"]),
    )
    validate_plan(plan)
    return plan


def _reset_state() -> None:
    _CONTEXT["keystore_path"] = None
    _CONTEXT["wallet_id"] = None
    _WALLETS.clear()
    _CONTROLLER.set_mode(ExecutionMode.SAFE)
    _CONTROLLER.disarm()
    global _LAST_PLAN, _LAST_DRY_RUN
    _LAST_PLAN = None
    _LAST_DRY_RUN = None
