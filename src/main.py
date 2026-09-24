"""
Google Trends Probe - measures how Google Trends answers from wherever this run's traffic exits.

Private, unpriced research actor. It replays the browser flow of trends.google.com/trends/explore
(warmup for the NID cookie, /api/explore, /api/widgetdata/multiline, optionally relatedsearches
and comparedgeo) for a list of keywords, one request at a time, with a fixed gap between Google
requests, and records every request as one dataset row. The OUTPUT record summarises it:
success rate, 429s and where the first one came, whether retries recovered, latency, points.

- One session = one cookie jar and one proxy session id (one exit IP). A new session every
  N keywords, and optionally after a 429, re-warms cookies and, behind a proxy, changes IP.
- Warmup is GET /trends/ (about 690 KB), the trending RSS (about 21 KB; it also sets NID, seen
  from a residential IP 2026-09-24) or nothing. The RSS and autocomplete readings come after
  the keywords, so warmup none really sends explore without a cookie.
- A 429 (or a redirect to google.com/sorry) on explore or multiline retries the whole keyword
  flow after a doubling backoff. Nothing else is retried: the probe measures, it does not hide.
- Proxy URLs hold the proxy password; they are never logged, stored or echoed.
- A status message that can never fail the run: SDK 3.x raises on the APIFY_AI run origin
  after the message is already stored.
"""
import asyncio
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from importlib.metadata import version
from urllib.parse import quote

from apify import Actor

from . import google as g
from .net import USERINFO, Session
from .stats import GOOGLE_ENDPOINTS, summarize

DEFAULTS = {
    'client': 'curl_cffi',
    'impersonate': 'chrome',
    'proxyConfiguration': {'useApifyProxy': False},
    'keywords': g.DEFAULT_KEYWORDS,
    'geo': '',
    'timeframe': 'today 12-m',
    'includeRelated': False,
    'warmup': 'home',
    'delayMs': 1500,
    'newSessionEvery': 10,
    'retry429': 2,
    'retryBackoffSecs': 10,
    'rotateOn429': True,
    'maxGoogleRequests': 300,
}
CLIENTS = ('curl_cffi', 'httpx')
WARMUPS = ('home', 'rss', 'none')
INT_RANGES = {'delayMs': (0, 60000), 'newSessionEvery': (0, 1000), 'retry429': (0, 10),
              'retryBackoffSecs': (0, 300), 'maxGoogleRequests': (1, 5000)}
BOOLS = ('includeRelated', 'rotateOn429')

BACKOFF_CAP_S = 120           # one 429 backoff never waits longer than this
TIME_MARGIN_S = 45            # stop starting keywords this close to the platform timeout
FLUSH_EVERY = 25              # dataset rows per push_data call
OUTPUT_EVERY_S = 60           # progress OUTPUT at most this often (each KV write is billed)
STATUS_EVERY_S = 15
SNIPPET_CHARS = 200
SORRY = 'google.com/sorry'
TLS_URL = 'https://tls.peet.ws/api/clean'


class BudgetSpent(Exception):
    """maxGoogleRequests reached."""


def read_input(raw: dict | None) -> dict:
    """Defaults for anything missing; ValueError with a plain sentence for anything unusable."""
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (raw or {}).items() if k in DEFAULTS and v is not None})
    if cfg['client'] not in CLIENTS:
        raise ValueError(f'client must be one of {", ".join(CLIENTS)}, not {cfg["client"]!r}.')
    if cfg['warmup'] not in WARMUPS:
        raise ValueError(f'warmup must be one of {", ".join(WARMUPS)}, not {cfg["warmup"]!r}.')
    if not isinstance(cfg['keywords'], list):
        raise ValueError('keywords must be a list of search terms.')
    kws = [str(k).strip() for k in cfg['keywords'] if str(k).strip()]
    cfg['keywords'] = list(dict.fromkeys(kws)) or list(g.DEFAULT_KEYWORDS)
    for k, (lo, hi) in INT_RANGES.items():
        try:
            v = int(cfg[k])
        except (TypeError, ValueError):
            raise ValueError(f'{k} must be a whole number.') from None
        if not lo <= v <= hi:
            raise ValueError(f'{k} must be between {lo} and {hi}, not {v}.')
        cfg[k] = v
    for k in BOOLS:
        cfg[k] = bool(cfg[k])
    cfg['geo'] = str(cfg['geo']).strip().upper()
    cfg['timeframe'] = str(cfg['timeframe']).strip() or DEFAULTS['timeframe']
    cfg['impersonate'] = str(cfg['impersonate']).strip() or DEFAULTS['impersonate']
    if not isinstance(cfg['proxyConfiguration'], dict):
        raise ValueError('proxyConfiguration must be an object, as the proxy editor produces.')
    return cfg


def echo_input(cfg: dict) -> dict:
    """The input as used, with any credentials inside custom proxy URLs masked."""
    out = json.loads(json.dumps(cfg))
    pc = out['proxyConfiguration']
    if pc.get('proxyUrls'):
        pc['proxyUrls'] = [USERINFO.sub('://***@', u) for u in pc['proxyUrls']]
    return out


def describe_proxy(pc: dict) -> str:
    if pc.get('useApifyProxy'):
        groups = '+'.join(pc.get('apifyProxyGroups') or []) or 'auto'
        country = pc.get('apifyProxyCountry')
        return f'apify:{groups}' + (f' country={country}' if country else '')
    if pc.get('proxyUrls'):
        return f'custom ({len(pc["proxyUrls"])} url)'
    return 'none'


def snippet(body: bytes) -> str | None:
    text = ' '.join(body[:4000].decode('utf-8', errors='replace').split())
    return text[:SNIPPET_CHARS] or None


def seconds_left_in_run() -> float | None:
    """Seconds until the platform kills this run, or None when not on the platform."""
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    timeout_at = getattr(config, 'timeout_at', None) if config else None
    if not timeout_at:
        return None
    return (timeout_at - datetime.now(timezone.utc)).total_seconds()


async def safe_status(message: str) -> None:
    try:
        await Actor.set_status_message(message[:500])
    except Exception as exc:  # noqa: BLE001
        Actor.log.debug(f'status message stored but not confirmed by the SDK: {exc}')


def parse_ipinfo(body: bytes) -> dict:
    d = json.loads(body)
    return {k: d.get(k) for k in ('ip', 'org', 'country', 'region', 'city', 'hostname')}


def parse_tls(body: bytes) -> dict:
    d = json.loads(body)
    return {k: d.get(k) for k in ('ja3_hash', 'ja4', 'akamai_hash', 'peetprint_hash')}


class Probe:
    def __init__(self, cfg: dict, proxy_cfg):
        self.cfg = cfg
        self.proxy_cfg = proxy_cfg
        self.tag = secrets.token_hex(3)
        self.t0 = time.monotonic()
        self.started = datetime.now(timezone.utc)
        self.rows: list[dict] = []
        self.buffer: list[dict] = []
        self.keyword_results: list[dict] = []
        self.sessions: list[dict] = []
        self.sess: Session | None = None
        self.session = None                # current entry of self.sessions
        self.google_count = 0
        self.last_google_end: float | None = None
        self.stop: str | None = None
        self.extras: dict = {'tlsFingerprint': None, 'rss': None, 'autocomplete': None}
        self.last_output = 0.0
        self.last_status = 0.0

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 1)

    # ------------------------------------------------------------------ requests --
    async def pace(self) -> None:
        if self.last_google_end is not None:
            wait = self.cfg['delayMs'] / 1000 - (time.monotonic() - self.last_google_end)
            if wait > 0:
                await asyncio.sleep(wait)

    async def call(self, endpoint: str, url: str, *, parse=None, keyword: str | None = None,
                   index: int | None = None, attempt: int = 1, headers: dict | None = None,
                   follow: bool = False) -> tuple[dict, object]:
        """One request, recorded as one row. Returns (row, parsed value or None)."""
        is_google = endpoint in GOOGLE_ENDPOINTS
        google_seq = None
        nid = None
        if is_google:
            if self.google_count >= self.cfg['maxGoogleRequests']:
                raise BudgetSpent
            await self.pace()
            self.google_count += 1
            google_seq = self.google_count
            nid = self.sess.has_cookie('NID')
        resp = await self.sess.get(url, headers=headers, follow=follow)
        if is_google:
            self.last_google_end = time.monotonic()

        value, parsed, error = None, False, resp.error
        if resp.status is not None and 200 <= resp.status < 300:
            if parse is None:
                parsed = True
            else:
                try:
                    value, parsed = parse(resp.body), True
                except Exception as exc:  # noqa: BLE001
                    error = f'parse: {type(exc).__name__}: {exc}'[:SNIPPET_CHARS]
        location = resp.headers.get('location')
        throttled = resp.status == 429 or SORRY in (location or '') or SORRY in resp.final_url
        ok = parsed and not throttled
        row = {
            'seq': len(self.rows) + 1,
            'googleSeq': google_seq,
            'google': is_google,
            'endpoint': endpoint,
            'keyword': keyword,
            'keywordIndex': index,
            'attempt': attempt,
            'status': resp.status,
            'ok': ok,
            'parsed': parsed,
            'throttled': throttled,
            'count': value if type(value) is int else (len(value) if endpoint == 'explore' and value else None),
            'latencyMs': resp.latency_ms,
            'bytes': len(resp.body),
            'bodySha1': hashlib.sha1(resp.body).hexdigest()[:12] if resp.body else None,  # same data twice?
            'httpVersion': resp.http_version,
            'redirects': resp.redirects,
            'location': self.sess.scrub(location)[:SNIPPET_CHARS] if location else None,
            'retryAfter': resp.headers.get('retry-after'),
            'error': error,
            'snippet': None if ok else snippet(resp.body),
            'nid': nid,
            'sessionNo': self.session['sessionNo'],
            'proxySession': self.session['proxySession'],
            'exitIp': self.session['exitIp'],
            'client': self.cfg['client'],
            'elapsedSecs': self.elapsed(),
            'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
        }
        self.rows.append(row)
        self.buffer.append(row)
        what = 'ok' if ok else ('THROTTLED' if throttled else 'FAIL')
        Actor.log.info(f'#{row["seq"]} {endpoint}{f" [{keyword}]" if keyword else ""} a{attempt} -> '
                       f'{resp.status} {resp.latency_ms} ms {len(resp.body)} B {what}'
                       + (f' count={row["count"]}' if row['count'] is not None else '')
                       + (f' | {error}' if error else ''))
        if len(self.buffer) >= FLUSH_EVERY:
            await self.flush()
        return row, value

    async def flush(self) -> None:
        if not self.buffer:
            return
        batch, self.buffer = self.buffer, []
        try:
            await Actor.push_data(batch)
        except Exception as exc:  # noqa: BLE001  the OUTPUT summary still holds the counts
            Actor.log.warning(f'Could not push {len(batch)} rows: {exc}')

    # ------------------------------------------------------------------ sessions --
    async def new_session(self) -> None:
        if self.sess:
            await self.sess.aclose()
        no = len(self.sessions) + 1
        sid = f'gtp{self.tag}_{no}'        # Apify allows [A-Za-z0-9._~], at most 50 chars
        proxy_url = await self.proxy_cfg.new_url(sid) if self.proxy_cfg else None
        self.sess = Session(self.cfg['client'], proxy_url, self.cfg['impersonate'])
        self.session = {'sessionNo': no, 'proxySession': sid if proxy_url else None, 'exitIp': None,
                        'org': None, 'country': None, 'city': None, 'warmupStatus': None,
                        'nidAfterWarmup': None, 'startedAtSecs': self.elapsed()}
        self.sessions.append(self.session)

        _, ip = await self.call('ipify', 'https://api.ipify.org?format=json', parse=lambda b: json.loads(b)['ip'])
        self.session['exitIp'] = ip
        _, info = await self.call('ipinfo', 'https://ipinfo.io/json', parse=parse_ipinfo)
        if info:
            self.session.update(org=info['org'], country=info['country'], city=info['city'])
            self.session['exitIp'] = self.session['exitIp'] or info['ip']
        if no == 1:
            _, self.extras['tlsFingerprint'] = await self.call('tls', TLS_URL, parse=parse_tls)

        warmup_url = {'home': g.WARMUP_URL, 'rss': g.rss_url(self.cfg['geo'])}.get(self.cfg['warmup'])
        if warmup_url:
            row, _ = await self.call('warmup', warmup_url, follow=True)
            self.session['warmupStatus'] = row['status']
        self.session['nidAfterWarmup'] = self.sess.has_cookie('NID')
        Actor.log.info(f'Session {no}: exit IP {self.session["exitIp"]} ({self.session["org"]}, '
                       f'{self.session["country"]}), warmup {self.session["warmupStatus"] or "skipped"}, NID cookie '
                       f'{"set" if self.session["nidAfterWarmup"] else "NOT set"}.')

    # ------------------------------------------------------------------ keywords --
    async def flow(self, index: int, kw: str, attempt: int, res: dict) -> str:
        """explore -> multiline (-> relatedsearches, comparedgeo). Returns ok, throttled or fail."""
        cfg = self.cfg
        hdr = {'Referer': f'{g.BASE}/trends/explore?q={quote(kw)}' + (f'&geo={cfg["geo"]}' if cfg['geo'] else '')}
        kw_args = {'keyword': kw, 'index': index, 'attempt': attempt, 'headers': hdr}
        row, widgets = await self.call('explore', g.explore_url(kw, cfg['geo'], cfg['timeframe']),
                                       parse=g.explore_widgets, **kw_args)
        if not row['ok']:
            return 'throttled' if row['throttled'] else 'fail'
        row, points = await self.call('multiline', g.widget_url('multiline', widgets['TIMESERIES']),
                                      parse=g.multiline_points, **kw_args)
        if not row['ok']:
            return 'throttled' if row['throttled'] else 'fail'
        res.update(ok=True, points=points)
        if cfg['includeRelated']:
            if 'RELATED_QUERIES' in widgets:
                await self.call('relatedsearches', g.widget_url('relatedsearches', widgets['RELATED_QUERIES']),
                                parse=g.related_count, **kw_args)
            if 'GEO_MAP' in widgets:
                await self.call('comparedgeo', g.widget_url('comparedgeo', widgets['GEO_MAP']),
                                parse=g.comparedgeo_count, **kw_args)
        return 'ok'

    async def keyword(self, index: int, kw: str) -> None:
        cfg = self.cfg
        res = {'keyword': kw, 'index': index, 'attempts': 0, 'throttled': False, 'ok': False, 'points': None}
        self.keyword_results.append(res)
        for attempt in range(1, cfg['retry429'] + 2):
            res['attempts'] = attempt
            if await self.flow(index, kw, attempt, res) != 'throttled':
                return
            res['throttled'] = True
            if attempt > cfg['retry429']:
                return
            wait = min(cfg['retryBackoffSecs'] * 2 ** (attempt - 1), BACKOFF_CAP_S)
            left = seconds_left_in_run()
            if left is not None and left - wait < TIME_MARGIN_S:
                self.stop = 'time'
                return
            Actor.log.warning(f'[{kw}] throttled on attempt {attempt}; waiting {wait} s'
                              + (', then a new session' if cfg['rotateOn429'] else '') + '.')
            await asyncio.sleep(wait)
            if cfg['rotateOn429']:
                await self.new_session()

    # ----------------------------------------------------------------------- run --
    async def run(self) -> None:
        cfg = self.cfg
        try:
            await self.new_session()
            every = cfg['newSessionEvery']
            for i, kw in enumerate(cfg['keywords']):
                left = seconds_left_in_run()
                if left is not None and left < TIME_MARGIN_S:
                    self.stop = 'time'
                if self.stop:
                    break
                if i and every and i % every == 0:
                    await self.new_session()
                await self.keyword(i, kw)
                await self.report()
            # After the keywords: the RSS sets NID, so earlier it would spoil warmup none.
            row, n = await self.call('rss', g.rss_url(cfg['geo']), parse=g.rss_items)
            self.extras['rss'] = {'status': row['status'], 'items': n}
            row, n = await self.call('autocomplete', g.autocomplete_url(cfg['keywords'][0]), parse=g.autocomplete_count,
                                     keyword=cfg['keywords'][0], headers={'Referer': f'{g.BASE}/trends/explore'})
            self.extras['autocomplete'] = {'status': row['status'], 'topics': n}
        except BudgetSpent:
            self.stop = 'maxGoogleRequests'
            Actor.log.warning(f'Stopped: maxGoogleRequests ({cfg["maxGoogleRequests"]}) reached.')
        finally:
            await self.flush()
            if self.sess:
                await self.sess.aclose()

    def output(self, status: str) -> dict:
        cfg = self.cfg
        return {
            'status': status,
            'stopReason': self.stop,
            'measuredAt': self.started.isoformat(timespec='seconds'),
            'elapsedSecs': self.elapsed(),
            'client': cfg['client'],
            'clientVersion': version(cfg['client']),
            'impersonate': cfg['impersonate'] if cfg['client'] == 'curl_cffi' else None,
            'proxy': describe_proxy(cfg['proxyConfiguration']),
            'googleRequests': self.google_count,
            **summarize(self.rows, self.keyword_results),
            **self.extras,
            'sessions': self.sessions,
            'keywordResults': self.keyword_results,
            'input': echo_input(cfg),
        }

    def headline(self) -> str:
        s = summarize(self.rows, self.keyword_results)
        gg, kw = s['google'], s['keywords']
        first = gg['firstThrottled']
        return (f'{kw["succeeded"]}/{len(self.cfg["keywords"])} keywords ok; Google {gg["ok"]}/{gg["requests"]} ok, '
                f'{gg["count429"]} x 429' + (f' (first at Google request #{first["googleSeq"]})' if first else '')
                + f'; {self.elapsed():.0f} s.')

    async def report(self) -> None:
        now = time.monotonic()
        if now - self.last_status >= STATUS_EVERY_S:
            self.last_status = now
            await safe_status(self.headline())
        if now - self.last_output >= OUTPUT_EVERY_S:
            self.last_output = now
            try:
                await Actor.set_value('OUTPUT', self.output('running'))
            except Exception as exc:  # noqa: BLE001
                Actor.log.warning(f'Could not refresh OUTPUT: {exc}')


async def main() -> None:
    async with Actor:
        try:
            cfg = read_input(await Actor.get_input())
        except ValueError as exc:
            await Actor.fail(status_message=str(exc))
            return
        proxy_desc = describe_proxy(cfg['proxyConfiguration'])
        Actor.log.info(f'Google Trends Probe: client={cfg["client"]} proxy={proxy_desc} '
                       f'keywords={len(cfg["keywords"])} delayMs={cfg["delayMs"]} '
                       f'newSessionEvery={cfg["newSessionEvery"]} retry429={cfg["retry429"]}')
        try:
            proxy_cfg = await Actor.create_proxy_configuration(actor_proxy_input=cfg['proxyConfiguration'])
        except Exception as exc:  # noqa: BLE001  a refused proxy group is a result, not a crash
            err = USERINFO.sub('://***@', f'{type(exc).__name__}: {exc}')[:500]
            Actor.log.warning(f'Proxy {proxy_desc} refused: {err}')
            await Actor.set_value('OUTPUT', {'status': 'proxy_refused', 'proxy': proxy_desc, 'proxyError': err,
                                             'measuredAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                             'input': echo_input(cfg)})
            await safe_status(f'Proxy {proxy_desc} refused: {err}')
            return

        probe = Probe(cfg, proxy_cfg)
        await probe.run()
        await Actor.set_value('OUTPUT', probe.output('done' if not probe.stop else 'stopped'))
        message = probe.headline()
        Actor.log.info(message)
        await safe_status(message)


if __name__ == '__main__':
    asyncio.run(main())
