"""Minimal FastAPI wrapper for the Thrive Truth Engine."""

from typing import Optional
import html
import json

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from core.engine import calculate_true_wealth
from core.models import PositionInput

app = FastAPI(title="Thrive Truth Engine")


def render_form(result_section: str = "") -> str:
    """Return a simple HTML page with the input form and optional result section."""

    description = (
        "Thrive reveals after-tax, real-world net wealth using the Truth Engine. "
        "Submit a position to see the liquidation reality."
    )
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>Thrive Truth Engine</title>
</head>
<body>
  <h1>Thrive Truth Engine</h1>
  <p>{html.escape(description)}</p>
  <form action="/calculate" method="post">
    <label>Asset type: <input name="asset_type" value="stock" required /></label><br />
    <label>Quantity: <input type="number" step="any" name="quantity" required /></label><br />
    <label>Cost basis per unit: <input type="number" step="any" name="cost_basis_per_unit" required /></label><br />
    <label>Current price: <input type="number" step="any" name="current_price" required /></label><br />
    <label>Days held: <input type="number" name="days_held" required /></label><br />
    <label>State tax rate (optional): <input type="number" step="any" name="state_tax_rate" /></label><br />
    <button type="submit">Calculate</button>
  </form>
  {result_section}
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    """Serve the input form."""

    return HTMLResponse(render_form())


@app.post("/calculate", response_class=HTMLResponse)
async def calculate(
    asset_type: str = Form(...),
    quantity: float = Form(...),
    cost_basis_per_unit: float = Form(...),
    current_price: float = Form(...),
    days_held: int = Form(...),
    state_tax_rate: Optional[str] = Form(None),
) -> HTMLResponse:
    """Accept form data, delegate to the engine, and render HTML output."""

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
  <p><a href="/">Back to form</a></p>
"""

    return HTMLResponse(render_form(result_section))
