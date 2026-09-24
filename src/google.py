"""
Talking to trends.google.com: sessions, pacing, the throttle policy, and the requests each report sends.

What real requests showed (2026-09-24, from a home IP and from Apify) and how this module handles it:

- /trends/api/explore answers 429 without the NID cookie, and any trends.google.com answer
  sets NID, even a 429. So every session starts with the 21 KB trending RSS (the /trends/
  page is 694 KB), and if that fails, the NID from explore's own 429 is used on the retry.
- The widget tokens explore hands out, and the NID, are bound to the IP that called explore.
  So everything for one keyword (or one comparison) goes through one session: one cookie jar
  and one proxy session id, which is one exit IP. A lane is a worker with one session at a time.
- One IP gets its first 429 at about 55-60 requests a minute. Requests in a session are spaced
  at least 1.5 s apart (end of one to start of the next), about 33 a minute.
- A 429 carries no Retry-After. Policy: wait 5 s and retry once in the same session. Throttled
  again, the keyword starts over in a fresh session (a new IP on a proxy lane, a new cookie jar
  on the direct lane), at most twice; parts already fetched are kept. A redirect to Google's
  block page (google.com/sorry), network errors and 5xx answers are treated the same way.
  Other answers (400, 401, 404) are not retried.
- tz is required (400 without it); tz=0 keeps Google's formatted strings in UTC. Bucket times
  are unix seconds on UTC boundaries whatever tz is.
- Proxy URLs hold the proxy password, so errors are scrubbed before they are logged.
"""
import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlsplit

import httpx
from apify import Actor

from . import parse

BASE = 'https://trends.google.com'
HL = 'en-US'
TZ = '0'
# Google did not care about the UA in tests (the NID cookie is what counts); a browser one is least odd.
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/140.0.0.0 Safari/537.36')
PACE_S = 1.5                  # gap between two requests of one session
RETRY_WAIT_S = 5.0            # wait before the one same-session retry after a 429
MAX_ROTATIONS = 2             # fresh sessions per keyword after the same-session retry failed
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 40           # the 7-day India Trending Now list (1.5 MB) took 11.3 s
USERINFO = re.compile(r'://[^/\s@]+@')
SORRY = 'google.com/sorry'    # Google's block page; a redirect there is a throttle, not an answer

# Test seams: tests swap these so pacing and backoff never really wait.
sleep = asyncio.sleep
clock = time.monotonic


class Throttled(Exception):
    """Google still throttled (or could not be reached) after the same-session retry."""


class GoogleError(Exception):
    """An answer retrying will not fix: 400, 401, 404, a redirect, or a body of the wrong shape."""


@dataclass
class Counters:
    """Requests to Google across all lanes, for the run summary."""
    requests: int = 0
    throttled: int = 0        # 429 answers
    failed: int = 0           # network errors, timeouts and 5xx answers


def _q(params: dict) -> str:
    return urlencode(params, quote_via=quote)


def _json(obj) -> str:
    """Compact and raw UTF-8, as the browser's JSON.stringify sends it."""
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False)


# ------------------------------------------------------------------------ URLs --
def explore_url(keywords: list[str], cfg) -> str:
    req = {'comparisonItem': [{'keyword': k, 'geo': cfg.geo, 'time': cfg.time_range} for k in keywords],
           'category': cfg.category, 'property': cfg.google_property}
    return f'{BASE}/trends/api/explore?' + _q({'hl': HL, 'tz': TZ, 'req': _json(req)})


def widget_url(kind: str, widget: dict) -> str:
    """kind: multiline, comparedgeo or relatedsearches. The widget's request goes back verbatim."""
    return f'{BASE}/trends/api/widgetdata/{kind}?' + _q({'hl': HL, 'tz': TZ, 'req': _json(widget['request']),
                                                         'token': widget['token']})


def rss_url(geo: str) -> str:
    return f'{BASE}/trending/rss?' + _q({'geo': geo or 'US'})


def autocomplete_url(keyword: str) -> str:
    return f'{BASE}/trends/api/autocomplete/{quote(keyword, safe="")}?' + _q({'hl': HL, 'tz': TZ})


BATCH_URL = f'{BASE}/_/TrendsUi/data/batchexecute?' + _q({'rpcids': 'i0OFE', 'source-path': '/trending',
                                                          'hl': HL, 'rt': 'c'})


def trending_form(geo: str, hours: int, news: int) -> dict:
    """f.req for rpc i0OFE. Its request is [null, null, geo, max news per trend, hl, hours]:
    index 3 = 0 gave no articles, 17 gave at most 17 per trend (2026-09-24)."""
    inner = _json([None, None, geo, news, HL, hours])
    return {'f.req': _json([[['i0OFE', inner, None, 'generic']]])}


# ------------------------------------------------------------------------ lane --
class Lane:
    """One worker's connection to Google: one session at a time (cookie jar + exit IP)."""

    def __init__(self, index: int, proxy_cfg, tag: str, warmup_geo: str, counters: Counters,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.index = index
        self.proxy_cfg = proxy_cfg
        self.tag = tag
        self.warmup_geo = warmup_geo
        self.counters = counters
        self.transport = transport
        self.client: httpx.AsyncClient | None = None
        self.sessions = 0
        self.requests = 0
        self.units = 0
        self.retired = False      # a proxy lane that Google or the proxy refused on every session
        self.last_end: float | None = None
        self.warmup_body: bytes | None = None
        self._secret: str | None = None

    @property
    def label(self) -> str:
        return 'direct lane' if self.proxy_cfg is None else f'proxy lane {self.index}'

    def scrub(self, text: str) -> str:
        text = USERINFO.sub('://***@', text)
        return text.replace(self._secret, '***') if self._secret else text

    async def open(self) -> None:
        """A fresh session: new cookie jar, new proxy session id (so a new IP), then the warmup."""
        await self.close()
        self.sessions += 1
        proxy_url = None
        if self.proxy_cfg is not None:
            # Apify allows [A-Za-z0-9._~] in a session id, at most 50 characters.
            proxy_url = await self.proxy_cfg.new_url(f'gt{self.tag}l{self.index}s{self.sessions}')
            self._secret = urlsplit(proxy_url).password if proxy_url else None
        self.client = httpx.AsyncClient(
            proxy=proxy_url, transport=self.transport, follow_redirects=False,
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            headers={'User-Agent': USER_AGENT, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9'})
        self.last_end = None
        self.warmup_body = None
        try:
            self.warmup_body = await self.get(rss_url(self.warmup_geo))
        except (Throttled, GoogleError) as exc:
            Actor.log.warning(f'{self.label}: warmup failed ({exc}); explore will pick up the cookie itself.')

    async def rotate(self, problem: str) -> None:
        how = 'a new IP' if self.proxy_cfg is not None else 'a new cookie jar (same IP, no proxy)'
        Actor.log.warning(f'{self.label}: {problem} after the retry; starting session {self.sessions + 1} with {how}.')
        await self.open()

    async def close(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):   # closing a dead proxy connection is not worth a failure
                await self.client.aclose()
            self.client = None

    async def _send(self, method: str, url: str, *, data: dict | None = None, referer: str | None = None
                    ) -> httpx.Response:
        """One paced request; after a 429, a network error or a 5xx, one retry in this session."""
        if self.client is None:
            await self.open()
        headers = {'Referer': referer} if referer else {}
        if data is not None:
            headers.update({'Origin': BASE, 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'})
        problem = ''
        for attempt in (1, 2):
            if self.last_end is not None:
                wait = PACE_S - (clock() - self.last_end)
                if wait > 0:
                    await sleep(wait)
            self.requests += 1
            self.counters.requests += 1
            resp = None
            try:
                resp = await self.client.request(method, url, data=data, headers=headers)
            except httpx.HTTPError as exc:          # connect, timeout, proxy, protocol and decoding errors
                problem = self.scrub(f'{type(exc).__name__}: {exc}' if str(exc) else type(exc).__name__)[:200]
                self.counters.failed += 1
            finally:
                self.last_end = clock()
            if resp is not None:
                if resp.status_code == 429 or SORRY in resp.headers.get('location', ''):
                    self.counters.throttled += 1
                    problem = f'Google throttled the request (HTTP {resp.status_code})'
                elif resp.status_code >= 500:
                    self.counters.failed += 1
                    problem = f'Google answered HTTP {resp.status_code}'
                else:
                    return resp
            if attempt == 1:
                Actor.log.warning(f'{self.label}: {problem}; retrying in {RETRY_WAIT_S:.0f} s in the same session.')
                await sleep(RETRY_WAIT_S)
        raise Throttled(problem)

    @staticmethod
    def _body(resp: httpx.Response) -> bytes:
        if resp.status_code != 200:
            where = resp.headers.get('location', '')
            to = f', a redirect to {where[:80]}' if where else ''
            raise GoogleError(f'Google answered HTTP {resp.status_code}{to}')
        return resp.content

    async def get(self, url: str, *, referer: str | None = None) -> bytes:
        return self._body(await self._send('GET', url, referer=referer))

    async def post(self, url: str, data: dict, *, referer: str | None = None) -> bytes:
        return self._body(await self._send('POST', url, data=data, referer=referer))


async def with_rotation(lane: Lane, attempt, may_rotate) -> object:
    """Run attempt() (every request of one unit). When Google still throttles after the
    same-session retry, open a fresh session and run it again, at most MAX_ROTATIONS times."""
    for rnd in range(MAX_ROTATIONS + 1):
        try:
            return await attempt()
        except Throttled as exc:
            if rnd == MAX_ROTATIONS or not may_rotate():
                raise
            await lane.rotate(str(exc))
    raise AssertionError('unreachable')


# -------------------------------------------------------------------- keywords --
@dataclass
class Explore:
    """What Google sent for one explore: one keyword, or one comparison of 2 to 5."""
    when: dict = field(default_factory=dict)            # startDate, endDate, step, intraday
    timeline: tuple | None = None                       # parse.timeline() result
    regions: list | None = None                         # parse.regions() result
    region_level: str | None = None
    related: dict[int, dict] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)   # part (or 'explore') -> why it is missing
    throttled: str | None = None                        # set when Google still throttled after every retry


async def _part(lane: Lane, out: Explore, part: str, url: str, referer: str, parse_fn):
    """Fetch and parse one widget. A refusal or a body of the wrong shape is noted, not raised;
    a throttle is raised so the whole keyword can move to a fresh session."""
    try:
        return parse_fn(await lane.get(url, referer=referer))
    except GoogleError as exc:
        out.problems[part] = str(exc)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        out.problems[part] = f'Google sent an answer of an unexpected shape ({type(exc).__name__})'
    return None


async def fetch_explore(lane: Lane, keywords: list[str], cfg, may_rotate) -> Explore:
    """explore, then the widgets for the parts asked for, all in one session. After a throttle
    the round starts over in a fresh session (new tokens) and fetches only what is still missing."""
    out = Explore()
    n = len(keywords)
    referer = parse.trends_url(keywords, cfg)

    async def one_round() -> None:
        body = await lane.get(explore_url(keywords, cfg), referer=referer)
        try:
            widgets = parse.explore_widgets(body)
        except (ValueError, KeyError, TypeError) as exc:
            raise GoogleError(f'Google sent no charts for this search ({exc})') from None
        out.when = parse.period(widgets)
        if cfg.wants('interestOverTime') and out.timeline is None and 'interestOverTime' not in out.problems:
            w = widgets.get('TIMESERIES')
            if w:
                out.timeline = await _part(lane, out, 'interestOverTime', widget_url('multiline', w), referer,
                                           lambda b: parse.timeline(b, n, out.when['intraday']))
        if cfg.wants('interestByRegion') and out.regions is None and 'interestByRegion' not in out.problems:
            w = widgets.get('GEO_MAP')
            if w:
                w = parse.with_resolution(w, cfg.google_resolution)
                out.region_level = parse.RESOLUTIONS.get(w['request'].get('resolution'))
                out.regions = await _part(lane, out, 'interestByRegion', widget_url('comparedgeo', w), referer,
                                          lambda b: parse.regions(b, n))
        if cfg.wants('relatedQueries'):
            for i in range(n):
                key = f'relatedQueries:{i}'
                w = widgets.get(parse.related_widget_id(i, n))
                if i in out.related or key in out.problems or not w:
                    continue
                got = await _part(lane, out, key, widget_url('relatedsearches', w), referer, parse.related)
                if got is not None:
                    out.related[i] = got

    try:
        await with_rotation(lane, one_round, may_rotate)
    except Throttled as exc:
        out.throttled = str(exc)
    except GoogleError as exc:
        out.problems['explore'] = str(exc)
    return out


# -------------------------------------------------------------------- trending --
async def fetch_trending(lane: Lane, cfg, may_rotate) -> tuple[list | None, bytes | None, str | None]:
    """(Google's full Trending Now list or None, the RSS feed for the fallback, the problem)."""
    referer = f'{BASE}/trending?' + _q({'geo': cfg.geo, 'hours': cfg.trending_hours})

    async def attempt() -> list:
        body = await lane.post(BATCH_URL, trending_form(cfg.geo, cfg.trending_hours, cfg.news_per_trend),
                               referer=referer)
        try:
            return parse.batch_items(body)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise GoogleError(str(exc)) from None

    try:
        return await with_rotation(lane, attempt, may_rotate), None, None
    except (Throttled, GoogleError) as exc:
        problem = str(exc)
    Actor.log.warning(f'Full Trending Now list failed ({problem}); falling back to Google\'s trending RSS feed.')
    rss = lane.warmup_body
    if rss is None:
        try:
            rss = await lane.get(rss_url(cfg.geo))
        except (Throttled, GoogleError) as exc:
            problem = f'{problem}; the RSS feed failed too ({exc})'
    return None, rss, problem


# ----------------------------------------------------------------- suggestions --
async def fetch_suggestions(lane: Lane, keyword: str, may_rotate) -> tuple[list | None, str | None, bool]:
    """(topics or None, the problem, whether Google still throttled after every retry)."""
    async def attempt() -> list:
        body = await lane.get(autocomplete_url(keyword), referer=f'{BASE}/trends/explore')
        try:
            return parse.autocomplete(body)
        except (ValueError, KeyError, TypeError) as exc:
            raise GoogleError(f'Google sent an answer of an unexpected shape ({type(exc).__name__})') from None

    try:
        return await with_rotation(lane, attempt, may_rotate), None, False
    except Throttled as exc:
        return None, str(exc), True
    except GoogleError as exc:
        return None, str(exc), False
