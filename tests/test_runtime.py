"""The runtime without the network: the 429 policy on a lane, and the charging rules of a whole run.

Run: python -m unittest discover -s tests -t .

FakeGoogle is an httpx.MockTransport handler that serves the saved answers in tests/fixtures/
(real Google bodies from 2026-09-24). Only Google's EMPTY answers are written here by hand, in
the shape Google uses for them ({"default":{"timelineData":[]}} and friends), because a
keyword with no data leaves nothing else to save.

FakeActor stands in for the SDK's pay-per-event side and follows SDK 3.4.1: push_data with a
charged event pushes only the rows the limit can pay for and charges them; charge() never goes
over the limit and says when no more of that event fits. Its push_data takes the event name
as a keyword only, as SDK 4 requires.
"""
import asyncio
import logging
import math
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from src import google, main
from src.inputs import parse_input

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
PRICES = {'keyword': '0.02', 'related-queries': '0.08', 'trend': '0.0009', 'suggestions': '0.002'}
NID = {'set-cookie': 'NID=from-google; Domain=.google.com; Path=/; Secure; HttpOnly'}
EMPTY_TIMELINE = b')]}\',\n{"default":{"timelineData":[],"averages":[]}}'
EMPTY_REGIONS = b')]}\',\n{"default":{"geoMapData":[]}}'
EMPTY_TOPICS = b')]}\',\n{"default":{"topics":[]}}'
FULL = {
    '/trending/rss': 'rss_trending_US.xml',
    '/trends/api/explore': 'explore_bitcoin_12m.txt',
    '/trends/api/widgetdata/multiline': 'multiline_bitcoin_12m.txt',
    '/trends/api/widgetdata/comparedgeo': 'comparedgeo_bitcoin_12m.txt',
    '/trends/api/widgetdata/relatedsearches': 'related_bitcoin_12m.txt',
    '/trends/api/autocomplete/': 'autocomplete_bitcoin.txt',
    '/_/TrendsUi/data/batchexecute': 'batchexecute_trending_US_24h_trimmed.txt',
}


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeGoogle:
    """routes: path prefix -> answer or list of answers (one per call, the last repeats). An answer is
    a fixture name, raw bytes, an HTTP status (int), or an exception to raise."""

    def __init__(self, routes: dict):
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((path, request.headers.get('cookie')))
        key = max((k for k in self.routes if path.startswith(k)), key=len, default=None)
        if key is None:
            raise AssertionError(f'unexpected request {path}')
        answers = self.routes[key]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, int):
            return httpx.Response(answer, text='<html>Error</html>', headers=NID)
        body = answer if isinstance(answer, bytes) else load(answer)
        return httpx.Response(200, content=body, headers=NID if path.startswith('/trending/rss') else {})

    def count(self, prefix: str) -> int:
        return sum(1 for p, _ in self.calls if p.startswith(prefix))


class FakeCharging:
    def __init__(self, ppe=True, max_usd='inf'):
        self.ppe = ppe
        self.prices = {k: Decimal(v) for k, v in PRICES.items()} if ppe else {}
        self.max = Decimal(max_usd)
        self.total = Decimal(0)

    def get_pricing_info(self):
        return SimpleNamespace(is_pay_per_event=self.ppe, per_event_prices=self.prices, max_total_charge_usd=self.max)

    def calculate_total_charged_amount(self) -> Decimal:
        return self.total

    def fits(self, event: str) -> int | None:
        price = self.prices.get(event)
        if not price or not self.max.is_finite():
            return None
        return max(0, math.floor((self.max - self.total) / price))

    def charge(self, event: str, count: int):
        if not self.ppe:
            return SimpleNamespace(event_charge_limit_reached=False, charged_count=0)
        fit = self.fits(event)
        done = count if fit is None else min(count, fit)
        self.total += done * self.prices.get(event, Decimal(0))
        after = self.fits(event)
        return SimpleNamespace(event_charge_limit_reached=after is not None and after <= 0, charged_count=done)


class FakeActor:
    def __init__(self, charging: FakeCharging):
        self.charging = charging
        self.rows: list[dict] = []
        self.charges: list[tuple[str, int]] = []

    def get_charging_manager(self):
        return self.charging

    async def push_data(self, data, *, charged_event_name=None):
        data = data if isinstance(data, list) else [data]
        if charged_event_name is None or not self.charging.ppe:
            self.rows += data
            return SimpleNamespace(event_charge_limit_reached=False, charged_count=0)
        fit = self.charging.fits(charged_event_name)
        n = len(data) if fit is None else min(len(data), fit)
        self.rows += data[:n]
        result = self.charging.charge(charged_event_name, n)
        self.charges.append((charged_event_name, result.charged_count))
        return result

    async def charge(self, event_name: str, count: int = 1):
        result = self.charging.charge(event_name, count)
        self.charges.append((event_name, result.charged_count))
        return result


class QuietSDK:
    """What main.py and google.py use of the Actor object outside the SDK context."""
    log = logging.getLogger('trends-test')
    log.addHandler(logging.NullHandler())
    log.propagate = False
    proxy = None          # a FakeProxy to give the run proxy lanes; None = no Apify Proxy

    async def create_proxy_configuration(self, *args, **kwargs):
        if self.proxy is None:
            raise RuntimeError('no Apify Proxy in tests')
        return self.proxy


class FakeProxy:
    """Stands in for Apify Proxy. new_url gives None, so the lane keeps the test transport (a real proxy
    URL would make httpx route around it) while the run still treats the lane as a proxy lane."""

    def __init__(self):
        self.sessions: list[str] = []

    async def new_url(self, session_id: str):
        self.sessions.append(session_id)


class Clock:
    """google.clock and google.sleep: sleeping moves the clock, nothing really waits. Every sleep
    yields to the event loop, so parallel lanes interleave as they do on the platform."""

    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.t += seconds
        await asyncio.sleep(0)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.sdk = sdk = QuietSDK()
        for patch in (mock.patch.object(google, 'sleep', self.clock.sleep),
                      mock.patch.object(google, 'clock', self.clock.now),
                      mock.patch.object(google, 'Actor', sdk),
                      mock.patch.object(main, 'Actor', sdk),
                      mock.patch.object(main.Run, 'report', new=mock.AsyncMock())):
            patch.start()
            self.addCleanup(patch.stop)

    async def run_actor(self, raw: dict, routes: dict | None = None, *, ppe=True, max_usd='inf',
                        proxy_routes: dict | None = None):
        """proxy_routes: give the run Apify Proxy lanes, answered by their own FakeGoogle (self.proxy_google)."""
        self.google = FakeGoogle({**FULL, **(routes or {})})
        self.actor = FakeActor(FakeCharging(ppe, max_usd))
        run = main.Run(parse_input(raw), main.Delivery(self.actor), transport=httpx.MockTransport(self.google))
        if proxy_routes is not None:
            self.sdk.proxy = FakeProxy()
            self.proxy_google = FakeGoogle({**FULL, **proxy_routes})
            opened = run.open_lanes

            async def open_lanes(units: int) -> None:
                await opened(units)
                for lane in run.lanes[1:]:
                    lane.transport = httpx.MockTransport(self.proxy_google)
            run.open_lanes = open_lanes
        await run.execute()
        return run

    def lane(self, routes: dict) -> google.Lane:
        self.google = FakeGoogle({**FULL, **routes})
        return google.Lane(0, None, 't', 'US', google.Counters(), httpx.MockTransport(self.google))


# ------------------------------------------------------------------------- lane --
class ThrottlePolicy(Base):
    async def test_warmup_sets_the_cookie_explore_sends(self):
        lane = self.lane({})
        await lane.get(google.explore_url(['bitcoin'], parse_input({'searchTerms': ['bitcoin']})))
        self.assertEqual([p for p, _ in self.google.calls], ['/trending/rss', '/trends/api/explore'])
        self.assertIsNone(self.google.calls[0][1])
        self.assertIn('NID=from-google', self.google.calls[1][1])

    async def test_pacing_between_requests(self):
        lane = self.lane({})
        await lane.get(google.rss_url('US'))
        await lane.get(google.rss_url('US'))
        self.assertEqual(self.clock.slept, [google.PACE_S, google.PACE_S])   # warmup -> 1st -> 2nd

    async def test_429_waits_5s_and_retries_in_the_same_session(self):
        lane = self.lane({'/trends/api/explore': [429, 'explore_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual(lane.sessions, 1)
        self.assertIn(google.RETRY_WAIT_S, self.clock.slept)
        self.assertEqual(self.google.count('/trends/api/explore'), 2)
        self.assertEqual(lane.counters.throttled, 1)
        self.assertEqual(len(got.related[0]['top']), 25)

    async def test_throttled_twice_moves_to_a_fresh_session_and_keeps_parts(self):
        lane = self.lane({'/trends/api/widgetdata/comparedgeo': [429, 429, 'comparedgeo_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual(lane.sessions, 2)
        self.assertEqual(self.google.count('/trending/rss'), 2)            # the new session warms up again
        self.assertEqual(self.google.count('/trends/api/explore'), 2)      # new tokens for the new session
        self.assertEqual(self.google.count('/trends/api/widgetdata/multiline'), 1)   # kept, not fetched again
        self.assertEqual(len(got.regions[0]), 64)

    async def test_gives_up_after_two_fresh_sessions(self):
        lane = self.lane({'/trends/api/explore': 429})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIn('429', got.throttled)
        self.assertEqual(lane.sessions, 1 + google.MAX_ROTATIONS)
        self.assertEqual(self.google.count('/trends/api/explore'), 2 * (1 + google.MAX_ROTATIONS))

    async def test_no_rotation_when_time_is_up(self):
        lane = self.lane({'/trends/api/explore': 429})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: False)
        self.assertIsNotNone(got.throttled)
        self.assertEqual(lane.sessions, 1)

    async def test_refusal_is_not_retried(self):
        lane = self.lane({'/trends/api/widgetdata/multiline': 401})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIn('HTTP 401', got.problems['interestOverTime'])
        self.assertEqual((lane.sessions, self.google.count('/trends/api/widgetdata/multiline')), (1, 1))
        self.assertEqual(len(got.regions[0]), 64)

    async def test_network_error_is_retried_like_a_throttle(self):
        lane = self.lane({'/trends/api/explore': [httpx.ConnectError('reset'), 'explore_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual(lane.counters.failed, 1)

    async def test_redirect_to_the_block_page_counts_as_a_throttle(self):
        sorry = httpx.Response(302, headers={'location': 'https://www.google.com/sorry/index?continue=x'})
        lane = self.lane({'/trends/api/explore': [sorry, 'explore_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual(lane.counters.throttled, 1)

    # A flagged IP answers every new cookie jar with a 302 to google.com/sorry (compare5 on Apify,
    # 2026-09-24: 0 of 5 keywords), so a fresh session must mean a new IP.
    def escalating_lane(self, routes: dict, tiers=('datacenter', 'residential')):
        lane = self.lane(routes)
        self.asked: list[str] = []
        proxies = {t: FakeProxy() for t in tiers}

        async def escalate(tier):
            self.asked.append(tier)
            return proxies.get(tier)
        lane.escalate = escalate
        return lane

    async def test_blocked_direct_lane_moves_to_the_datacenter_proxy(self):
        sorry = httpx.Response(302, headers={'location': 'https://www.google.com/sorry/index?continue=x'})
        lane = self.escalating_lane({'/trends/api/explore': [sorry, sorry, 'explore_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual((lane.tier, lane.sessions, self.asked), ('datacenter', 2, ['datacenter']))
        self.assertTrue(lane.is_direct)                   # still lane 0: never hands work back

    async def test_last_rotation_goes_to_the_residential_proxy(self):
        lane = self.escalating_lane({'/trends/api/explore': [429, 429, 429, 429, 'explore_bitcoin_12m.txt']})
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual((lane.tier, lane.sessions), ('residential', 3))
        self.assertEqual(self.asked, ['datacenter', 'residential'])

    async def test_no_residential_keeps_the_datacenter_proxy(self):
        lane = self.escalating_lane({'/trends/api/explore': 429}, tiers=('datacenter',))
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIn('429', got.throttled)
        self.assertEqual((lane.tier, lane.sessions), ('datacenter', 1 + google.MAX_ROTATIONS))

    async def test_no_proxy_at_all_falls_back_to_a_new_cookie_jar(self):
        lane = self.escalating_lane({'/trends/api/explore': [429, 429, 'explore_bitcoin_12m.txt']}, tiers=())
        got = await google.fetch_explore(lane, ['bitcoin'], parse_input({'searchTerms': ['bitcoin']}), lambda: True)
        self.assertIsNone(got.throttled)
        self.assertEqual((lane.tier, lane.sessions, lane.proxy_cfg), ('direct', 2, None))

    async def test_run_escalates_through_the_sdk_proxy(self):
        sorry = httpx.Response(302, headers={'location': 'https://www.google.com/sorry/index?continue=x'})
        self.sdk.proxy = FakeProxy()
        run = await self.run_actor({'searchTerms': ['bitcoin']},
                                   {'/trends/api/explore': [sorry, sorry, 'explore_bitcoin_12m.txt']})
        self.assertEqual(len(self.actor.rows), 1)
        self.assertEqual(run.lanes[0].tier, 'datacenter')
        self.assertTrue(self.sdk.proxy.sessions)

    async def test_proxy_password_is_scrubbed(self):
        lane = google.Lane(1, None, 't', 'US', google.Counters())
        lane._secret = 's3cret'  # noqa: S105  a made-up proxy password
        self.assertEqual(lane.scrub('ProxyError: http://user:s3cret@proxy:8000 said s3cret'),
                         'ProxyError: http://***@proxy:8000 said ***')


# ---------------------------------------------------------------------- charging --
class KeywordCharging(Base):
    async def test_example_input_charges_keyword_and_related_queries(self):
        run = await self.run_actor({'mode': 'keywords', 'searchTerms': ['bitcoin', 'chatgpt']})
        self.assertEqual([r['keyword'] for r in self.actor.rows], ['bitcoin', 'chatgpt'])
        self.assertEqual(self.actor.charges, [('keyword', 1), ('related-queries', 1)] * 2)
        self.assertEqual(run.counters.requests, 1 + 2 * 4)       # warmup, then explore + 3 widgets per keyword
        self.assertEqual([e['status'] for e in run.entries], ['ok', 'ok'])

    async def test_empty_related_queries_are_not_charged(self):
        run = await self.run_actor({'searchTerms': ['bitcoin']},
                                   {'/trends/api/widgetdata/relatedsearches': 'related_empty.txt'})
        self.assertEqual(self.actor.charges, [('keyword', 1)])
        self.assertEqual(self.actor.rows[0]['missing'], ['relatedQueries'])
        self.assertEqual(self.actor.rows[0]['relatedQueries'], {'top': [], 'rising': []})
        self.assertEqual(run.entries[0]['status'], 'partial')

    async def test_no_data_is_not_pushed_or_charged(self):
        run = await self.run_actor({'searchTerms': ['qzxqv']},
                                   {'/trends/api/widgetdata/multiline': EMPTY_TIMELINE,
                                    '/trends/api/widgetdata/comparedgeo': EMPTY_REGIONS,
                                    '/trends/api/widgetdata/relatedsearches': 'related_empty.txt'})
        self.assertEqual((self.actor.rows, self.actor.charges), ([], []))
        self.assertEqual((run.entries[0]['status'], run.entries[0]['reasonCode']), ('skipped', 'no_data'))
        self.assertEqual(run.delivery.rows, 0)

    async def test_related_queries_alone_skip_the_keyword_event(self):
        await self.run_actor({'searchTerms': ['bitcoin'], 'dataTypes': ['relatedQueries']})
        self.assertEqual(self.actor.charges, [('related-queries', 1)])
        self.assertEqual(self.actor.rows[0]['interestOverTime'], [])
        self.assertEqual(self.google.count('/trends/api/widgetdata/multiline'), 0)

    async def test_reason_names_the_throttle_only_for_the_part_it_blocked(self):
        run = await self.run_actor({'searchTerms': ['bitcoin']},
                                   {'/trends/api/widgetdata/comparedgeo': EMPTY_REGIONS,
                                    '/trends/api/widgetdata/relatedsearches': 429})
        self.assertEqual(self.actor.charges, [('keyword', 1)])
        self.assertEqual(self.actor.rows[0]['missing'], ['interestByRegion', 'relatedQueries'])
        reason = run.entries[0]['reason']
        self.assertIn('interest by region (Google sent none)', reason)
        self.assertIn('related queries (Google throttled the request (HTTP 429) after every retry)', reason)

    async def test_throttled_keyword_is_skipped_and_free(self):
        run = await self.run_actor({'searchTerms': ['bitcoin']}, {'/trends/api/explore': 429})
        self.assertEqual((self.actor.rows, self.actor.charges), ([], []))
        self.assertEqual(run.entries[0]['reasonCode'], 'throttled')

    async def test_compare_charges_each_keyword(self):
        run = await self.run_actor({'mode': 'compare', 'searchTerms': ['bitcoin', 'ethereum', 'solana'], 'geo': 'US'},
                                   {'/trends/api/explore': 'explore_5coins_US_7d_news.txt',
                                    '/trends/api/widgetdata/multiline': 'multiline_5coins_US_7d_news.txt',
                                    '/trends/api/widgetdata/comparedgeo': 'comparedgeo_5coins_US_7d_news.txt'})
        self.assertEqual([r['comparedWith'] for r in self.actor.rows],
                         [['ethereum', 'solana'], ['bitcoin', 'solana'], ['bitcoin', 'ethereum']])
        self.assertEqual([r['averageInterest'] for r in self.actor.rows], [28, 8, 7])
        self.assertEqual(self.actor.charges, [('keyword', 1), ('related-queries', 1)] * 3)
        self.assertEqual(run.counters.requests, 1 + 3 + 3)   # warmup, explore + timeline + map, 3 related


class SpendingLimit(Base):
    async def test_limit_stops_before_fetching_what_it_cannot_pay_for(self):
        run = await self.run_actor({'searchTerms': ['bitcoin', 'chatgpt', 'python']}, max_usd='0.15')
        self.assertEqual(len(self.actor.rows), 1)
        self.assertEqual(self.google.count('/trends/api/explore'), 1)
        self.assertEqual(run.stop, 'limit')
        self.assertEqual([e['reasonCode'] for e in run.entries], ['ok', 'limit', 'limit'])
        self.assertIn('spending limit', run.message(True))

    async def test_no_free_related_queries_at_the_edge_of_the_limit(self):
        # After the first keyword $0.09 is left: the next keyword event ($0.02) fits, its related queries ($0.08)
        # would not. Without the reservation the run would fetch it and deliver related queries it cannot charge.
        run = await self.run_actor({'searchTerms': ['bitcoin', 'chatgpt']}, max_usd='0.19')
        self.assertEqual(len(self.actor.rows), 1)
        self.assertEqual(self.google.count('/trends/api/explore'), 1)
        self.assertEqual(self.actor.charges, [('keyword', 1), ('related-queries', 1)])
        self.assertEqual(run.stop, 'limit')

    async def test_charge_result_at_the_limit_stops_after_the_current_keyword(self):
        run = await self.run_actor({'searchTerms': [f'k{i}' for i in range(7)], 'dataTypes': ['interestOverTime']},
                                   max_usd='0.10')
        self.assertEqual(len(self.actor.rows), 5)
        self.assertEqual(self.google.count('/trends/api/explore'), 5)
        self.assertEqual(run.stop, 'limit')
        self.assertEqual(sum(e['status'] == 'ok' for e in run.entries), 5)

    async def test_parallel_lanes_do_not_stop_while_budget_is_only_held(self):
        # $0.25, and every keyword comes back without related queries: each holds $0.10 while it is
        # fetched but costs $0.02. Two lanes each holding $0.10 must not end the run: a lane that finds
        # the budget held by the other steps aside instead, so the run gets as far as one lane would.
        empty = {'/trends/api/widgetdata/relatedsearches': 'related_empty.txt'}
        run = await self.run_actor({'searchTerms': [f'k{i}' for i in range(10)]}, empty, max_usd='0.25',
                                   proxy_routes=empty)
        self.assertEqual(len(self.actor.rows), 8)       # as one lane: 8 x $0.02, then $0.09 left < $0.10 worst case
        self.assertEqual(self.actor.charges, [('keyword', 1)] * 8)
        self.assertEqual(run.stop, 'limit')
        self.assertEqual([e['reasonCode'] for e in run.entries].count('limit'), 2)
        self.assertEqual(run.delivery.reserved, 0)

    async def test_not_pay_per_event_counts_events_without_a_limit(self):
        run = await self.run_actor({'searchTerms': ['bitcoin', 'chatgpt']}, ppe=False)
        self.assertEqual(len(self.actor.rows), 2)
        self.assertEqual((run.delivery.charged['keyword'], run.delivery.charged['related-queries']), (2, 2))
        self.assertIsNone(run.stop)


class TrendingCharging(Base):
    async def test_every_trend_is_one_event(self):
        run = await self.run_actor({'mode': 'trending'})
        self.assertEqual(len(self.actor.rows), 7)
        self.assertEqual(self.actor.charges, [('trend', 7)])
        self.assertEqual(run.trending['source'], 'full list')
        self.assertEqual(run.counters.requests, 2)

    async def test_limit_cuts_the_list(self):
        run = await self.run_actor({'mode': 'trending'}, max_usd='0.0045')
        self.assertEqual(len(self.actor.rows), 5)
        self.assertEqual((run.stop, run.trending['saved']), ('limit', 5))

    async def test_a_limit_below_one_trend_says_so(self):
        run = await self.run_actor({'mode': 'trending'}, max_usd='0.0005')
        self.assertEqual((self.actor.rows, run.stop), ([], 'limit'))
        message = run.message(True)
        self.assertIn('Google listed 7 Trending Now searches for United States, but none were saved', message)
        self.assertIn('spending limit', message)
        self.assertNotIn('could be read', message)

    async def test_empty_answers_are_told_apart(self):
        run = await self.run_actor({'mode': 'trending'},
                                   {'/_/TrendsUi/data/batchexecute': b')]}\'\n\n9\n[["wrb.fr","i0OFE","[null,[]]"]]'})
        self.assertIn('Google listed no Trending Now searches for United States', run.message(True))
        empty_feed = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        run = await self.run_actor({'mode': 'trending'}, {'/_/TrendsUi/data/batchexecute': 400,
                                                          '/trending/rss': empty_feed})
        self.assertIn('No Trending Now searches could be read for United States (Google answered HTTP 400',
                      run.message(True))

    async def test_topic_filter_and_cap(self):
        await self.run_actor({'mode': 'trending', 'trendingCategory': 'technology'})
        self.assertEqual([r['term'] for r in self.actor.rows], ['meta vr glasses', 'xbox'])
        await self.run_actor({'mode': 'trending', 'maxItems': 3})
        self.assertEqual(len(self.actor.rows), 3)

    async def test_rss_fallback(self):
        run = await self.run_actor({'mode': 'trending'}, {'/_/TrendsUi/data/batchexecute': 400})
        self.assertEqual(len(self.actor.rows), 10)
        self.assertEqual(run.trending['source'], 'rss feed')
        self.assertIn('HTTP 400', run.trending['problem'])
        self.assertEqual(self.google.count('/trending/rss'), 1)            # the warmup body is reused
        await self.run_actor({'mode': 'trending', 'trendingCategory': 'sports'}, {'/_/TrendsUi/data/batchexecute': 400})
        self.assertEqual(self.actor.rows, [])


class SuggestionCharging(Base):
    async def test_topics_are_charged_empty_is_free(self):
        run = await self.run_actor({'mode': 'suggestions', 'searchTerms': ['bitcoin', 'zzzz']},
                                   {'/trends/api/autocomplete/zzzz': EMPTY_TOPICS})
        self.assertEqual([r['keyword'] for r in self.actor.rows], ['bitcoin'])
        self.assertEqual(self.actor.charges, [('suggestions', 1)])
        self.assertEqual([e['reasonCode'] for e in run.entries], ['ok', 'no_data'])


class Lanes(Base):
    def test_lane_count(self):
        self.assertEqual([main.proxy_lane_count(n) for n in (1, 8, 9, 16, 17, 40, 41, 1000)], [0, 0, 1, 1, 2, 4, 5, 5])

    async def test_no_proxy_means_one_lane_for_everything(self):
        run = await self.run_actor({'searchTerms': [f'k{i}' for i in range(9)], 'dataTypes': ['interestOverTime']})
        self.assertEqual(len(run.lanes), 1)
        self.assertEqual(len(self.actor.rows), 9)

    async def test_proxy_lanes_share_the_work(self):
        run = await self.run_actor({'searchTerms': [f'k{i}' for i in range(10)], 'dataTypes': ['interestOverTime']},
                                   proxy_routes={})
        self.assertEqual(len(run.lanes), 2)
        self.assertEqual(sorted(r['keyword'] for r in self.actor.rows), sorted(f'k{i}' for i in range(10)))
        self.assertGreater(run.lanes[1].units, 0)
        self.assertGreater(run.lanes[0].units, 0)
        self.assertEqual(self.sdk.proxy.sessions[0][-4:], 'l1s1')

    async def test_a_broken_proxy_lane_hands_its_keyword_back_and_retires(self):
        # Every request through the proxy fails (a 407, a dead pool or Google refusing its IPs): the keyword
        # it held is fetched again on the direct lane, and the proxy lane takes no more work.
        refused = httpx.ProxyError('407 Proxy Authentication Required')
        run = await self.run_actor({'searchTerms': [f'k{i}' for i in range(10)], 'dataTypes': ['interestOverTime']},
                                   proxy_routes=dict.fromkeys(FULL, refused))
        self.assertEqual(sorted(r['keyword'] for r in self.actor.rows), sorted(f'k{i}' for i in range(10)))
        self.assertEqual([e['status'] for e in run.entries], ['ok'] * 10)
        self.assertTrue(run.lanes[1].retired)
        self.assertEqual(run.lanes[1].units, 0)
        self.assertEqual(run.lanes[1].sessions, 1 + google.MAX_ROTATIONS)
        self.assertEqual(self.actor.charges, [('keyword', 1)] * 10)

    async def test_the_direct_lane_never_hands_back(self):
        run = await self.run_actor({'searchTerms': ['bitcoin']}, {'/trends/api/explore': 429})
        self.assertEqual(run.entries[0]['reasonCode'], 'throttled')
        self.assertFalse(run.lanes[0].retired)


class TimeLimit(Base):
    async def test_no_new_keyword_inside_the_margin(self):
        left = iter([600.0, 600.0, 600.0, 30.0])        # Run.__init__, then one check per keyword or rotation
        with mock.patch.object(main, 'seconds_left_in_run', lambda: next(left, 30.0)):
            run = await self.run_actor({'searchTerms': ['k0', 'k1', 'k2'], 'dataTypes': ['interestOverTime']})
        self.assertEqual([e['reasonCode'] for e in run.entries], ['ok', 'ok', 'time'])
        self.assertEqual(run.stop, 'time')
        self.assertIn('time limit', run.message(True))

    async def test_a_refused_rotation_does_not_claim_the_run_stopped_early(self):
        # The last keyword is throttled near the end: no fresh session, but nothing was left unstarted.
        with mock.patch.object(main, 'seconds_left_in_run', lambda: 600.0):
            run = main.Run(parse_input({'searchTerms': ['bitcoin']}), main.Delivery(FakeActor(FakeCharging())))
        with mock.patch.object(main, 'seconds_left_in_run', lambda: 30.0):
            self.assertFalse(run.time_left())
        self.assertIsNone(run.stop)


if __name__ == '__main__':
    unittest.main()
