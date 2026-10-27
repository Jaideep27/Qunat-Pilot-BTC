"""Serve the public read-only view on 127.0.0.1:8090 for a tunnel (Tailscale Funnel) to publish.

Only this port may be published. Never publish 8000 (the API) or 5173 (the owner's dashboard).
"""
from pathlib import Path
import sys

import uvicorn

from quantpilot.api.public import create_app

dist = Path(__file__).resolve().parents[2]/'frontend'/'dist'
if not (dist/'index.html').exists():
    sys.exit('Build the dashboard first: npm --prefix frontend run build')
uvicorn.run(create_app(dist), host='127.0.0.1', port=8090, server_header=False)
