import sys
from pathlib import Path

# Ensure repo root is on sys.path for Vercel serverless runtime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.app import app

# Vercel ASGI entrypoint
handler = app
