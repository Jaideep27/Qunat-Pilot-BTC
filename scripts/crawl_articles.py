"""Fetch article text for news URLs with Crawl4AI. Runs in its own environment (.venv-crawl) with `python -I`.

Reads {"urls": [...], "max_chars": N, "mode": "http"|"browser"} as JSON on stdin and prints one line,
`QP-JSON:` followed by a JSON list of {url, ok, title, markdown, error}. Page content is untrusted
data: it is returned as text and never executed, followed or interpreted. Only https URLs, at most 20 per
call; the HTTP strategy (no browser) is the default to keep memory low.
"""
import asyncio
import contextlib
import io
import json
import sys

MARKER = 'QP-JSON:'


async def crawl(job):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, HTTPCrawlerConfig
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
    urls = [u for u in job.get('urls', []) if isinstance(u, str) and u.startswith('https://')][:20]
    limit = max(1000, min(int(job.get('max_chars', 20000)), 100_000))
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20_000, verbose=False)
    if job.get('mode') == 'browser':
        crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
    else:
        crawler = AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy(
            browser_config=HTTPCrawlerConfig(method='GET', follow_redirects=True)))
    out = []
    async with crawler:
        for url in urls:
            try:
                result = await crawler.arun(url=url, config=config)
                text = str(result.markdown or '') if result.success else ''
                out.append(dict(url=url, ok=bool(result.success and text.strip()), title=(result.metadata or {}).get('title'),
                                markdown=text[:limit], error=None if result.success else str(result.error_message)[:300]))
            except Exception as exc:   # one bad page never stops the batch
                out.append(dict(url=url, ok=False, title=None, markdown='', error=type(exc).__name__))
    return out


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    job = json.loads(sys.stdin.read() or '{}')
    noise = io.StringIO()
    with contextlib.redirect_stdout(noise):   # library progress output must not corrupt the result line
        out = asyncio.run(crawl(job))
    sys.stdout.write(MARKER+json.dumps(out)+'\n')


if __name__ == '__main__':
    main()
