"""Public read-only gateway: the built dashboard plus an allow-listed set of GET reads from the local API.

A tunnel (Tailscale Funnel) delivers every visitor from 127.0.0.1, so the API's loopback check cannot tell a
visitor from the owner. This process is the boundary instead: GET/HEAD only, listed read paths only, no AI,
no chat history, no API docs, brief caching and a per-visitor rate limit so visitors cannot load the laptop.
"""
import asyncio
import time
from collections import OrderedDict, deque
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

READS = frozenset({
    'health', 'ready', 'operations', 'candles', 'positioning', 'sentiment', 'news', 'forecasts', 'strategy-research',
    'archive', 'macro', 'coverage', 'backtests', 'market/storage', 'market/candles', 'market/studies',
    # The analysis app: reads only; the Claude explain/brief POSTs stay local-only.
    'analysis/snapshot', 'analysis/levels', 'analysis/summary', 'analysis/catalogue', 'analysis/briefs',
    'analysis/explanations'})
MARKER = '<meta name="quantpilot-public" content="1"/>'
HEADERS = {
    'Content-Security-Policy': "default-src 'self'; script-src 'self' https://s3.tradingview.com; "
        "frame-src https:; connect-src 'self' wss://fstream.binance.com; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    'X-Robots-Tag': 'noindex, nofollow', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'}


def create_app(dist, upstream='http://127.0.0.1:8000', transport=None, ttl=15.0, per_minute=240,
               concurrent=4, clock=time.monotonic):
    dist = Path(dist).resolve()
    cache, visits = OrderedDict(), {}
    gate = asyncio.Semaphore(concurrent)
    api = httpx.AsyncClient(base_url=upstream, transport=transport, timeout=30)

    def allowed(request):
        """Sliding one-minute window per visitor; the tunnel's X-Forwarded-For names the visitor when it is sent."""
        key = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '-')
        now = clock()
        if len(visits) > 10000:
            for stale in [k for k, times in visits.items() if not times or times[-1] < now-60]: del visits[stale]
        times = visits.setdefault(key, deque())
        while times and times[0] <= now-60: times.popleft()
        if len(times) >= per_minute: return False
        times.append(now)
        return True

    async def read(request, path, query):
        if len(query) > 2000: return JSONResponse({'detail': 'Query too long'}, 414)
        key = path+'?'+query
        hit = cache.get(key)
        if hit and hit[0] > clock(): return Response(hit[1], 200, media_type='application/json')
        # Only calls that reach the API count: cached reads cost the laptop nothing.
        if not allowed(request): return JSONResponse({'detail': 'Too many requests; try again in a minute'}, 429)
        async with gate:
            try:
                result = await api.get('/api/v1/'+path, params=httpx.QueryParams(query))
            except httpx.HTTPError:
                return JSONResponse({'detail': 'QuantPilot is offline right now'}, 503)
        if result.status_code == 200:
            cache[key] = (clock()+ttl, result.content)
            cache.move_to_end(key)
            while len(cache) > 512: cache.popitem(last=False)
        return Response(result.content, result.status_code, media_type='application/json')

    def page():
        html = (dist/'index.html').read_text(encoding='utf-8').replace('<head>', '<head>'+MARKER, 1)
        return HTMLResponse(html, headers={'Cache-Control': 'no-cache'})

    async def handle(request: Request):
        path = request.path_params['path']
        if path.startswith('api/v1/'):
            if path[7:] not in READS: return JSONResponse({'detail': 'Not available in the public view'}, 404)
            return await read(request, path[7:], request.url.query)
        if path in ('', 'index.html'): return page()
        if path.startswith('assets/'):
            file = (dist/path).resolve()
            if file.is_relative_to(dist/'assets') and file.is_file():
                return FileResponse(file, headers={'Cache-Control': 'public, max-age=31536000, immutable'})
        return JSONResponse({'detail': 'Not found'}, 404)

    async def secure(scope, receive, send):
        async def headers(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', [])
                message['headers'] += [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]
            await send(message)
        await app(scope, receive, headers if scope['type'] == 'http' else send)

    app = Starlette(routes=[Route('/{path:path}', handle, methods=['GET', 'HEAD'])])
    return secure
