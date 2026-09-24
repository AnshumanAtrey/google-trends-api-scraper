"""
One HTTP session under test: httpx (Python TLS, HTTP/1.1) or curl_cffi (Chrome TLS and HTTP/2).

A session is one cookie jar plus one proxy session id, so one exit IP. Rotating means closing
it and opening a new one. Both clients get byte-identical URLs (built in google.py) and the
same explicit headers; curl_cffi adds Chrome's own headers on top, which is the point of it.

Proxy URLs carry the proxy password, so they are never logged or stored, and every error
text is scrubbed before it reaches the log or the dataset.
"""
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from curl_cffi import requests as curl

# httpx sends Python's TLS fingerprint under a browser User-Agent: what a naive scraper looks like.
CHROME_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
             'Chrome/150.0.0.0 Safari/537.36')
BASE_HEADERS = {'Accept-Language': 'en-US,en;q=0.9'}
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 20
MAX_REDIRECTS = 5
CURL_HTTP_VERSIONS = {1: 'HTTP/1.0', 2: 'HTTP/1.1', 3: 'HTTP/2', 30: 'HTTP/3'}
USERINFO = re.compile(r'://[^/\s@]+@')


@dataclass
class Resp:
    status: int | None                     # None: no HTTP answer (connect error, timeout, proxy refused)
    latency_ms: int
    body: bytes = b''
    headers: dict = field(default_factory=dict)
    final_url: str = ''
    redirects: int = 0
    http_version: str | None = None
    error: str | None = None


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


class Session:
    def __init__(self, kind: str, proxy_url: str | None, impersonate: str):
        self.kind = kind
        self._secret = urlsplit(proxy_url).password if proxy_url else None
        if kind == 'httpx':
            self._c = httpx.AsyncClient(
                proxy=proxy_url, follow_redirects=False,
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                headers={'User-Agent': CHROME_UA, **BASE_HEADERS})
        elif kind == 'curl_cffi':
            self._c = curl.AsyncSession(
                impersonate=impersonate, proxy=proxy_url, allow_redirects=False,
                timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S), headers=BASE_HEADERS)
        else:
            raise ValueError(f'unknown client {kind!r}; use httpx or curl_cffi')

    def scrub(self, text: str) -> str:
        text = USERINFO.sub('://***@', text)
        return text.replace(self._secret, '***') if self._secret else text

    def has_cookie(self, name: str) -> bool:
        return any(c.name == name for c in self._c.cookies.jar)

    async def get(self, url: str, headers: dict | None = None, follow: bool = False) -> Resp:
        t0 = time.perf_counter()
        try:
            if self.kind == 'httpx':
                r = await self._c.get(url, headers=headers, follow_redirects=follow)
                return Resp(r.status_code, _ms(t0), r.content, {k.lower(): v for k, v in r.headers.items()},
                            str(r.url), len(r.history), r.http_version)
            r = await self._c.get(url, headers=headers, allow_redirects=follow, max_redirects=MAX_REDIRECTS)
            return Resp(r.status_code, _ms(t0), r.content, {k.lower(): v for k, v in r.headers.items()},
                        str(r.url), r.redirect_count, CURL_HTTP_VERSIONS.get(r.http_version, str(r.http_version)))
        except Exception as exc:  # noqa: BLE001  a probe records failures, it never raises them
            return Resp(None, _ms(t0), error=self.scrub(f'{type(exc).__name__}: {exc}')[:300])

    async def aclose(self) -> None:
        try:
            await (self._c.aclose() if self.kind == 'httpx' else self._c.close())
        except Exception:  # noqa: BLE001, S110  closing a dead proxy connection is not worth a failure
            pass
