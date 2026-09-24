"""
Google Trends Scraper API - Google Trends data as dataset rows, with no Google account or API key.

Four reports, set by mode:
- keywords: each keyword on its own 0-100 scale: interest over time, interest by region and
  related queries (top and rising), whichever are asked for. One row per keyword.
- compare: 2 to 5 keywords in one explore, so they share one scale. One row per keyword.
- trending: every Trending Now search in a country (Google's full list, not the 10-item RSS),
  with news articles. One row per trend. The RSS feed is the fallback if the list fails.
- suggestions: the topics (and topic ids) Google matches each keyword to. One row per keyword.

How the run is organised (the request-level rules are in google.py):
- Lane 0 talks to Google directly. With more than 8 keywords, up to 5 more lanes run in
  parallel on Apify datacenter proxy sessions (each its own IP); if the proxy cannot be set up,
  everything stays on lane 0. Each keyword runs start to finish on one lane. A proxy lane that
  gets nothing but throttles or proxy errors for a keyword, even on fresh sessions, retires and
  hands the keyword back, so a bad proxy pool costs time, never keywords.
- No new keyword starts within 60 s of the run's time limit, so the summary always gets written.
- Charging (pay per event): a keyword row is charged "keyword" only when it holds a timeline or
  regions; "related-queries" once per keyword only when Google sent at least one related query;
  one "trend" per Trending Now row; one "suggestions" per keyword with at least one topic. A row
  with no data is not pushed and costs nothing; OUTPUT says why. Before a keyword is fetched its
  worst-case cost is reserved against the spending limit, so parallel lanes never deliver data the
  limit cannot pay for; a lane that finds the rest of the budget held by other lanes steps aside
  rather than ending the run, and the limit counts as reached only when nothing is held. When a
  charge says the limit is reached the run stops after that item.
- No summary row in the dataset (CSV and Excel exports hold data rows only). The summary is the
  OUTPUT record, refreshed at most every 10 s (each key-value write is billed) and final at the end.
- A run that delivers nothing ends FAILED with a plain message; a partial one SUCCEEDED.
- The status message can never fail the run: SDK 3.x raises on the APIFY_AI run origin after
  the message is already stored.
"""
import asyncio
import secrets
import time
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal

from apify import Actor

from . import google, parse
from .inputs import Config, InputError, geo_name, parse_input

# Pay-per-event names; they must equal the event keys of the pricing set in Console / store.json.
KEYWORD_EVENT = 'keyword'
RELATED_EVENT = 'related-queries'
TREND_EVENT = 'trend'
SUGGESTIONS_EVENT = 'suggestions'
EVENTS = (KEYWORD_EVENT, RELATED_EVENT, TREND_EVENT, SUGGESTIONS_EVENT)

KEYWORDS_PER_LANE = 8         # one more proxy lane for every 8 keywords after the first 8
MAX_PROXY_LANES = 5
RUN_SAFETY_MARGIN_S = 60      # stop starting keywords this close to the time limit (a quarter of short runs)
OUTPUT_EVERY_S = 10
STATUS_EVERY_S = 5
TREND_BATCH = 200             # Trending Now rows per push, so a spending limit cuts cleanly

PART_NAMES = {'interestOverTime': 'interest over time', 'interestByRegion': 'interest by region',
              'relatedQueries': 'related queries'}
STOP_REASONS = {
    'limit': 'your spending limit for this run was reached. Raise the limit to get the rest.',
    'time': 'the run was about to reach its time limit. Raise the run timeout to get the rest.',
}


async def safe_status(message: str) -> None:
    """Set the run's status message without ever failing the run (see the module docstring)."""
    try:
        await Actor.set_status_message(message[:500])
    except Exception as exc:  # noqa: BLE001
        Actor.log.debug(f'status message stored but not confirmed by the SDK: {exc}')


def seconds_left_in_run() -> float | None:
    """Seconds until the platform kills this run, or None when not on the platform."""
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    timeout_at = getattr(config, 'timeout_at', None) if config else None
    if not timeout_at:
        return None
    return (timeout_at - datetime.now(timezone.utc)).total_seconds()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def proxy_lane_count(units: int) -> int:
    return min(MAX_PROXY_LANES, (units - 1) // KEYWORDS_PER_LANE) if units > KEYWORDS_PER_LANE else 0


TIME_WORDS = {'now 1-H': 'past hour', 'now 4-H': 'past 4 hours', 'now 1-d': 'past day', 'now 7-d': 'past 7 days',
              'today 1-m': 'past 30 days', 'today 3-m': 'past 90 days', 'today 12-m': 'past 12 months',
              'today 5-y': 'past 5 years', 'all': 'since 2004'}


def describe_period(cfg: Config) -> str:
    period = TIME_WORDS.get(cfg.time_range) or cfg.time_range.replace(' ', ' to ')
    return f'{period}, {geo_name(cfg.geo) if cfg.geo else "worldwide"}'


# --------------------------------------------------------------------- charging --
class Delivery:
    """Pushes rows, charges events, counts what was charged and notices the spending limit."""

    def __init__(self, actor=Actor):
        self.actor = actor
        self.cm = actor.get_charging_manager()
        info = self.cm.get_pricing_info()
        self.ppe = info.is_pay_per_event
        self.prices = info.per_event_prices
        self.max_usd = info.max_total_charge_usd
        self.reserved = Decimal(0)
        self.charged = Counter(dict.fromkeys(EVENTS, 0))
        self.rows = 0
        self.limit = False

    def reserve(self, events: list[str]) -> Decimal | None:
        """Hold the worst-case cost of one unit against the spending limit; None when it does not fit.
        The limit counts as reached only when nothing else is held: budget held by a unit in flight on
        another lane may come back (a keyword without related queries costs less than its worst case)."""
        if self.limit:
            return None
        if not self.ppe or self.max_usd is None or not Decimal(self.max_usd).is_finite():
            return Decimal(0)
        need = sum((Decimal(self.prices.get(e, 0)) for e in events), Decimal(0))
        left = Decimal(self.max_usd) - self.cm.calculate_total_charged_amount() - self.reserved
        if need > left:
            if not self.reserved:
                self.limit = True
            return None
        self.reserved += need
        return need

    def release(self, amount: Decimal) -> None:
        self.reserved -= amount

    def _count(self, event: str, result, asked: int) -> int:
        done = result.charged_count if self.ppe else asked
        self.charged[event] += done
        if self.ppe and (result.event_charge_limit_reached or done < asked):
            self.limit = True
        return done

    async def push(self, rows: list[dict], event: str) -> int:
        """Push rows charged as event. Returns how many were delivered: the SDK drops rows it cannot charge."""
        result = await self.actor.push_data(rows, charged_event_name=event)
        done = self._count(event, result, len(rows))
        self.rows += done
        return done

    async def keyword_row(self, row: dict, has_core: bool, has_related: bool) -> bool:
        """Deliver one keyword row by the charging rules. False when it was not delivered."""
        if has_core:
            if not await self.push([row], KEYWORD_EVENT):
                return False
        elif has_related:
            await self.actor.push_data([row])     # related queries alone: no keyword event
            self.rows += 1
        else:
            return False
        if has_related:
            self._count(RELATED_EVENT, await self.actor.charge(event_name=RELATED_EVENT, count=1), 1)
        return True


# -------------------------------------------------------------------------- run --
class Run:
    def __init__(self, cfg: Config, delivery: Delivery, transport=None):
        self.cfg = cfg
        self.delivery = delivery
        self.transport = transport           # test seam: httpx mock transport
        self.t0 = time.monotonic()
        self.tag = secrets.token_hex(3)
        self.counters = google.Counters()
        self.lanes: list[google.Lane] = []
        self.proxy_cfgs: dict = {}
        self.stop: str | None = None
        self.trending: dict | None = None
        self.last_output = 0.0
        self.last_status = 0.0
        self.entries = [{'keyword': k, 'status': 'waiting', 'reasonCode': None, 'reason': None, 'missing': [],
                         'lane': None} for k in cfg.keywords] if cfg.mode != 'trending' else []
        self.entries += [{'keyword': k, 'status': 'skipped', 'reasonCode': 'invalid', 'reason': f'Invalid: {why}',
                          'missing': [], 'lane': None} for k, why in cfg.invalid] if cfg.mode != 'trending' else []
        left = seconds_left_in_run()
        self.margin = RUN_SAFETY_MARGIN_S if left is None else min(RUN_SAFETY_MARGIN_S, left / 4)

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 1)

    def time_left(self) -> bool:
        """False once the run is inside its safety margin. Used to refuse fresh sessions late in a run."""
        left = seconds_left_in_run()
        return left is None or left >= self.margin

    def time_ok(self) -> bool:
        """time_left(), and when time is up, the run stops starting units (checked only while units are left)."""
        if self.time_left():
            return True
        self.stop = self.stop or 'time'
        return False

    # ---------------------------------------------------------------- lanes --
    async def open_lanes(self, units: int) -> None:
        warm = self.cfg.geo or 'US'
        self.lanes = [google.Lane(0, None, self.tag, warm, self.counters, self.transport, self.proxy_for)]
        extra = proxy_lane_count(units)
        if not extra:
            return
        proxy_cfg = await self.proxy_for('datacenter')
        if proxy_cfg is None:
            return
        self.lanes += [google.Lane(i, proxy_cfg, self.tag, warm, self.counters, self.transport, self.proxy_for)
                       for i in range(1, extra + 1)]
        Actor.log.info(f'{units} keywords: {len(self.lanes)} lanes (direct + {extra} on Apify datacenter proxy).')

    async def proxy_for(self, tier: str):
        """The Apify proxy configuration for a tier ('datacenter' or 'residential'), created once;
        None when the account or plan does not have it (then that tier is simply skipped)."""
        if tier not in self.proxy_cfgs:
            try:
                self.proxy_cfgs[tier] = await (Actor.create_proxy_configuration(groups=['RESIDENTIAL'])
                                               if tier == 'residential' else Actor.create_proxy_configuration())
            except Exception as exc:  # noqa: BLE001  a missing proxy tier means fewer options, not a failed run
                Actor.log.warning(f'Apify {tier} proxy is not available ({type(exc).__name__}: {str(exc)[:200]}).')
                self.proxy_cfgs[tier] = None
        return self.proxy_cfgs[tier]

    async def close_lanes(self) -> None:
        for lane in self.lanes:
            await lane.close()

    async def run_units(self, units: list, handle) -> None:
        """Lanes take units from one queue. A handler returns False to hand its unit back untouched
        (a proxy lane Google refused on every session, or budget held by another lane): the unit goes
        back to the front of the queue and that lane takes no more work in this pass. Whatever is left
        when the lanes are done runs on the direct lane, which never hands a unit back."""
        queue = deque(units)

        async def worker(lane: google.Lane) -> None:
            while queue and not self.stop and not lane.retired and self.time_ok():
                unit = queue.popleft()
                try:
                    if await handle(lane, unit) is False:
                        queue.appendleft(unit)
                        break
                except Exception as exc:  # noqa: BLE001  one keyword must never crash the run
                    Actor.log.exception(f'{lane.label}: unexpected error')
                    if self.cfg.mode == 'trending':
                        self.trending = {**(self.trending or {}), 'problem': f'unexpected error ({type(exc).__name__})'}
                    for e in unit if isinstance(unit, list) else [unit] if isinstance(unit, dict) else []:
                        if e['status'] in ('waiting', 'running'):
                            self.skip(e, 'error', f'Not saved because of an unexpected error ({type(exc).__name__}: '
                                                  f'{str(exc)[:150]}). This is on us, not you: please report it.')
                lane.units += 1
                await self.report()

        await asyncio.gather(*(worker(lane) for lane in self.lanes))
        if queue and not self.stop:
            await worker(self.lanes[0])
        for e in self.entries:
            if e['status'] in ('waiting', 'running'):
                self.skip(e, self.stop or 'time', f'Not fetched: {STOP_REASONS[self.stop or "time"]}')

    @staticmethod
    def skip(entry: dict, code: str, reason: str) -> None:
        entry.update(status='skipped', reasonCode=code, reason=reason)

    def no_budget(self) -> bool | None:
        """After a failed reservation: None when the limit is reached (the run stops), False when the
        budget is only held by units in flight on other lanes (this lane hands its unit back)."""
        if self.delivery.limit:
            self.stop = 'limit'
            return None
        return False

    def hand_back(self, lane: google.Lane, entries: list[dict], problem: str) -> bool:
        """True when a proxy lane got nothing but throttles for a unit: the lane retires and the unit
        goes back to the queue for the other lanes and, in the end, the direct lane."""
        if lane.is_direct:
            return False
        lane.retired = True
        for e in entries:
            e.update(status='waiting', lane=None)
        Actor.log.warning(f'{lane.label}: {problem} on every session; it takes no more keywords and '
                          f'"{entries[0]["keyword"]}" goes back to the queue.')
        return True

    # ------------------------------------------------------------- keywords --
    async def keyword_unit(self, lane: google.Lane, unit: list[dict]) -> bool | None:
        cfg = self.cfg
        keywords = [e['keyword'] for e in unit]
        events = ([KEYWORD_EVENT] if cfg.wants('interestOverTime') or cfg.wants('interestByRegion') else []) \
            + ([RELATED_EVENT] if cfg.wants('relatedQueries') else [])
        reserved = self.delivery.reserve(events * len(unit))
        if reserved is None:
            return self.no_budget()
        try:
            for e in unit:
                e.update(status='running', lane=lane.index)
            got = await google.fetch_explore(lane, keywords, cfg, self.time_left)
            nothing = got.timeline is None and got.regions is None and not got.related
            if got.throttled and nothing and self.hand_back(lane, unit, got.throttled):
                return False
            scraped = now_iso()
            for i, entry in enumerate(unit):
                await self.deliver_keyword(entry, i, keywords, got, scraped, lane)
        finally:
            self.delivery.release(reserved)
        return None

    async def deliver_keyword(self, entry: dict, i: int, keywords: list[str], got: google.Explore, scraped: str,
                              lane: google.Lane) -> None:
        cfg = self.cfg
        missing: list[str] = []
        why: dict[str, str] = {}

        def lack(part: str, fetched: bool, key: str | None = None) -> None:
            """Note a part asked for but empty, with why: Google's refusal, a throttle that outlasted every
            retry (only for parts never fetched), or Google answering with nothing."""
            missing.append(part)
            problem = got.problems.get(key or part) or got.problems.get('explore')
            if not problem and not fetched and got.throttled:
                problem = f'{got.throttled} after every retry'
            why[part] = problem or 'Google sent none'

        points = average = None
        if cfg.wants('interestOverTime'):
            if got.timeline and got.timeline[1][i]:
                points, average = got.timeline[0][i], got.timeline[2][i]
            else:
                lack('interestOverTime', got.timeline is not None)
        region_rows = None
        if cfg.wants('interestByRegion'):
            if got.regions and got.regions[i]:
                region_rows = got.regions[i]
            else:
                lack('interestByRegion', got.regions is not None)
        related = None
        if cfg.wants('relatedQueries'):
            rq = got.related.get(i)
            if rq and (rq['top'] or rq['rising']):
                related = rq
            else:
                lack('relatedQueries', i in got.related, f'relatedQueries:{i}')

        has_core, has_related = bool(points or region_rows), related is not None
        kw = entry['keyword']
        if not has_core and not has_related:
            entry['missing'] = missing
            if got.problems.get('explore'):
                self.skip(entry, 'error', f'Not saved or charged: {got.problems["explore"]}.')
            elif got.throttled:
                self.skip(entry, 'throttled', f'Not saved or charged: {got.throttled} even after a retry and '
                                              f'{google.MAX_ROTATIONS} fresh sessions. Run it again later.')
            else:
                self.skip(entry, 'no_data', 'Not saved or charged: no data from Google. It has too little search '
                                            'interest for this keyword in this place and time.')
            Actor.log.warning(f'[{kw}] {entry["reason"]}')
            return

        row = parse.keyword_row(kw, keywords, cfg, got.when, scraped, points=points, average=average,
                                region_level=got.region_level if cfg.wants('interestByRegion') else None,
                                region_rows=region_rows, related_queries=related, missing=missing)
        if not await self.delivery.keyword_row(row, has_core, has_related):
            self.skip(entry, 'limit', f'Not saved: {STOP_REASONS["limit"]}')
            self.stop = 'limit'
            return
        if self.delivery.limit:
            self.stop = 'limit'
        entry['missing'] = missing
        if missing:
            entry.update(status='partial', reasonCode='ok', reason='Saved without ' + '; '.join(
                f'{PART_NAMES[p]} ({why[p]})' for p in missing) + '.')
        else:
            entry.update(status='ok', reasonCode='ok', reason=None)
        rq = related or {'top': [], 'rising': []}
        Actor.log.info(f'[{kw}] saved on the {lane.label}: {len(points or [])} points, '
                       f'{len(region_rows or [])} regions, '
                       f'{len(rq["top"])} top + {len(rq["rising"])} rising queries'
                       + (f'; missing {", ".join(missing)}' if missing else ''))

    # ------------------------------------------------------------- trending --
    async def trending_unit(self, lane: google.Lane, _unit) -> None:
        cfg = self.cfg
        items, rss, problem = await google.fetch_trending(lane, cfg, self.time_left)
        scraped = now_iso()
        info = {'geo': cfg.geo, 'geoName': geo_name(cfg.geo), 'trendingHours': cfg.trending_hours,
                'trendingCategory': cfg.trending_category, 'source': None, 'found': 0, 'afterFilter': 0,
                'saved': 0, 'problem': problem, 'note': None}
        self.trending = info
        rows: list[dict] = []
        if items is not None:
            info['source'] = 'full list'
            info['found'] = len(items)
            keep = [it for it in items if parse.has_topic(it, cfg.trending_category_id)]
            info['afterFilter'] = len(keep)
            rows = [parse.trend_row(it, cfg.geo, cfg.trending_hours, cfg.news_per_trend, scraped) for it in keep]
        elif rss:
            info['source'] = 'rss feed'
            try:
                rows = parse.rss_rows(rss, cfg.geo, cfg.news_per_trend, scraped)
            except Exception as exc:  # noqa: BLE001  ElementTree.ParseError or an odd feed
                info['problem'] = f'{problem}; the RSS feed could not be read ({type(exc).__name__})'
                rows = []
            info['found'] = len(rows)
            if cfg.trending_category_id is not None:
                info['note'] = ('Google\'s full Trending Now list failed and its RSS feed has no topics, so nothing '
                                f'could be filtered to {cfg.trending_category}. Run it again, or choose every topic.')
                rows = []
            elif rows:
                info['note'] = ('Google\'s full Trending Now list failed, so these are the trends in Google\'s RSS '
                                'feed (about 10, without growth, topics or end time).')
            info['afterFilter'] = len(rows)
        if cfg.max_items:
            rows = rows[:cfg.max_items]
        for start in range(0, len(rows), TREND_BATCH):
            batch = rows[start:start + TREND_BATCH]
            info['saved'] += await self.delivery.push(batch, TREND_EVENT)
            if self.delivery.limit:
                self.stop = 'limit'
                break
        Actor.log.info(f'Trending Now {cfg.geo} {cfg.trending_hours} h: {info["found"]} found ({info["source"]}), '
                       f'{info["afterFilter"]} after the topic filter, {info["saved"]} saved.')

    # ----------------------------------------------------------- suggestions --
    async def suggestion_unit(self, lane: google.Lane, entry: dict) -> bool | None:
        reserved = self.delivery.reserve([SUGGESTIONS_EVENT])
        if reserved is None:
            return self.no_budget()
        try:
            entry.update(status='running', lane=lane.index)
            topics, problem, throttled = await google.fetch_suggestions(lane, entry['keyword'], self.time_left)
            if throttled and self.hand_back(lane, [entry], problem):
                return False
            if not topics:
                if throttled:
                    self.skip(entry, 'throttled', f'Not saved or charged: {problem} even after a retry and '
                                                  f'{google.MAX_ROTATIONS} fresh sessions. Run it again later.')
                elif problem:
                    self.skip(entry, 'error', f'Not saved or charged: {problem}.')
                else:
                    self.skip(entry, 'no_data', 'Not saved or charged: no data from Google. It matched no topics to '
                                                'this keyword.')
                Actor.log.warning(f'[{entry["keyword"]}] {entry["reason"]}')
                return
            row = {'keyword': entry['keyword'], 'suggestions': topics, 'scrapedAt': now_iso()}
            if not await self.delivery.push([row], SUGGESTIONS_EVENT):
                self.skip(entry, 'limit', f'Not saved: {STOP_REASONS["limit"]}')
                self.stop = 'limit'
                return
            if self.delivery.limit:
                self.stop = 'limit'
            entry.update(status='ok', reasonCode='ok', reason=None)
            Actor.log.info(f'[{entry["keyword"]}] {len(topics)} topics on the {lane.label}.')
        finally:
            self.delivery.release(reserved)

    # --------------------------------------------------------------- summary --
    def counts(self) -> dict:
        by = Counter(e['status'] for e in self.entries)
        return {'asked': len(self.entries), 'saved': by['ok'] + by['partial'], 'partial': by['partial'],
                'skipped': by['skipped']}

    def message(self, final: bool) -> str:
        cfg = self.cfg
        c = self.counts()
        took = f'{self.counters.requests} requests to Google in {self.elapsed():.0f} s.'
        if cfg.mode == 'trending':
            t = self.trending or {}
            if not final:
                return 'Reading Google\'s Trending Now list.'
            if t.get('saved'):
                topic = f', topic {cfg.trending_category}' if cfg.trending_category != 'all' else ''
                head = (f'Saved {t["saved"]:,} Trending Now searches for {geo_name(cfg.geo)}, '
                        f'past {cfg.trending_hours} hours{topic}.')
            elif t.get('source') == 'full list' and not t.get('found'):
                head = (f'Google listed no Trending Now searches for {geo_name(cfg.geo)} in the past '
                        f'{cfg.trending_hours} hours.')
            elif t.get('found') and self.stop == 'limit':
                head = (f'Google listed {t["found"]:,} Trending Now searches for {geo_name(cfg.geo)}, but none were '
                        f'saved.')
            elif t.get('found') and not t.get('afterFilter'):
                head = (f'Google listed {t["found"]:,} trends for {geo_name(cfg.geo)}, but none under the topic '
                        f'{cfg.trending_category}.')
            else:
                why = t.get('problem') or 'no answer'
                head = f'No Trending Now searches could be read for {geo_name(cfg.geo)} ({why}).'
            parts = [head] + ([t['note']] if t.get('note') else [])
        else:
            what = 'topic suggestions for' if cfg.mode == 'suggestions' else 'data for'
            verb = 'Saved' if final else 'So far saved'
            scope = '' if cfg.mode == 'suggestions' else f' ({describe_period(cfg)})'
            parts = [f'{verb} {what} {c["saved"]} of {c["asked"]} keywords{scope}.']
            if c['partial']:
                parts.append(f'{c["partial"]} of them without some parts, listed in "missing".')
            if final:
                stopped = [e for e in self.entries if e['reasonCode'] in ('limit', 'time')]
                skipped = [e for e in self.entries if e['status'] == 'skipped' and e not in stopped]
                for e in skipped[:5]:
                    parts.append(f'"{e["keyword"]}": {e["reason"]}')
                if len(skipped) > 5:
                    parts.append(f'{len(skipped) - 5} more skipped, see OUTPUT.')
                if stopped:
                    parts.append(f'{len(stopped)} keyword{"s were" if len(stopped) > 1 else " was"} not saved.')
        if self.stop and final:
            parts.append(f'The run stopped early because {STOP_REASONS[self.stop]}')
        parts.append(took)
        return ' '.join(parts)

    def output(self, status: str) -> dict:
        d = self.delivery
        return {
            'status': status,
            'notes': self.cfg.notes,
            'message': self.message(status != 'running'),
            'mode': self.cfg.mode,
            'stopReason': self.stop,
            'counts': self.counts() if self.cfg.mode != 'trending' else None,
            'trending': self.trending,
            'rowsSaved': d.rows,
            'chargedEvents': dict(d.charged),
            'payPerEvent': d.ppe,
            'googleRequests': self.counters.requests,
            'throttledRequests': self.counters.throttled,
            'failedRequests': self.counters.failed,
            'elapsedSecs': self.elapsed(),
            'lanes': [{'lane': ln.index, 'proxy': ln.proxy_cfg is not None, 'tier': ln.tier, 'sessions': ln.sessions,
                       'requests': ln.requests, 'units': ln.units, 'retired': ln.retired} for ln in self.lanes],
            'keywords': self.entries,
        }

    async def report(self, final: bool = False) -> None:
        now = time.monotonic()
        if final or now - self.last_status >= STATUS_EVERY_S:
            self.last_status = now
            await safe_status(self.message(final))
        if final or now - self.last_output >= OUTPUT_EVERY_S:
            self.last_output = now
            status = ('done' if self.delivery.rows else 'failed') if final else 'running'
            try:
                await Actor.set_value('OUTPUT', self.output(status))
            except Exception as exc:  # noqa: BLE001
                Actor.log.warning(f'Could not write the OUTPUT record: {exc}')

    async def execute(self) -> None:
        cfg = self.cfg
        try:
            if cfg.mode == 'trending':
                await self.open_lanes(1)
                await self.run_units([None], self.trending_unit)
            elif cfg.mode == 'compare':
                await self.open_lanes(1)
                await self.run_units([[e for e in self.entries if e['status'] == 'waiting']], self.keyword_unit)
            else:
                units = [e for e in self.entries if e['status'] == 'waiting']
                await self.open_lanes(len(units))
                handle = self.suggestion_unit if cfg.mode == 'suggestions' else \
                    (lambda lane, e: self.keyword_unit(lane, [e]))
                await self.run_units(units, handle)
        finally:
            await self.close_lanes()


async def main() -> None:
    async with Actor:
        raw = await Actor.get_input()
        try:
            cfg = parse_input(raw)
        except InputError as exc:
            await Actor.set_value('OUTPUT', {'status': 'failed', 'message': str(exc)})
            await Actor.fail(status_message=str(exc))
            return
        delivery = Delivery()
        Actor.log.info(f'Google Trends Scraper API: mode={cfg.mode} keywords={len(cfg.keywords)} geo={cfg.geo or "-"} '
                       f'timeRange="{cfg.time_range}" dataTypes={",".join(cfg.data_types)} '
                       f'property={cfg.search_property} '
                       f'category={cfg.category} payPerEvent={delivery.ppe} maxTotalChargeUsd={delivery.max_usd}')
        for kw, why in cfg.invalid:
            Actor.log.warning(f'Skipped "{kw}": {why}')
        for note in cfg.notes:
            Actor.log.warning(note)
        run = Run(cfg, delivery)
        await run.execute()
        message = run.message(True)
        Actor.log.info(message)
        await run.report(final=True)
        if not delivery.rows:
            await Actor.fail(status_message=message[:500])


if __name__ == '__main__':
    asyncio.run(main())
