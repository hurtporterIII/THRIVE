import html
import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from core.engine import calculate_truth
from core.models import PositionInput

app = FastAPI()

@app.post("/calculate")
def calculate(**kwargs):
    # Adapter translation: add ticker but do NOT remove asset_type
    if "asset_type" in kwargs and "ticker" not in kwargs:
        kwargs["ticker"] = kwargs["asset_type"]

    position = PositionInput(**kwargs)
    result = calculate_truth(position).to_dict()
    escaped_json = html.escape(json.dumps(result, indent=2))

    return HTMLResponse(content=f"<pre>{escaped_json}</pre>", media_type="text/html")

@app.get("/")
def root():
    return {"status": "ok"}
