"""Offline checks: URL building, body parsers, stats, input handling and the 429 retry loop.

Run: .venv/bin/python -m unittest discover -s tests
"""
import asyncio
import json
import pathlib
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from src import google as g
from src import main as m
from src import stats
from src.net import Resp, Session

ROOT = pathlib.Path(__file__).resolve().parent.parent
TS_REQ = {'time': '2025-09-24 2026-09-24', 'resolution': 'WEEK', 'locale': 'en-US',
          'comparisonItem': [{'geo': {}, 'complexKeywordsRestriction': {
              'keyword': [{'type': 'BROAD', 'value': 'fußball'}]}}],
          'requestOptions': {'property': '', 'backend': 'IZG', 'category': 0}}
EXPLORE = (")]}'\n" + json.dumps({'widgets': [
    {'id': 'TIMESERIES', 'token': 'T1', 'request': TS_REQ},
    {'id': 'GEO_MAP', 'token': 'T2', 'request': {'geo': {}}},
    {'id': 'RELATED_TOPICS', 'token': 'T3', 'request': {}},
    {'id': 'RELATED_QUERIES', 'token': 'T4', 'request': {'restriction': {}}},
]})).encode()
MULTILINE = (")]}',\n" + json.dumps({'default': {'timelineData': [{'time': str(i), 'value': [i]} for i in range(53)],
                                                'averages': []}})).encode()
RELATED = (")]}',\n" + json.dumps({'default': {'rankedList': [{'rankedKeyword': [{}, {}, {}]},
                                                              {'rankedKeyword': [{}]}]}})).encode()
RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item><title>a</title></item>' \
      b'<item><title>b</title></item></channel></rss>'


class GoogleTests(unittest.TestCase):
    def test_explore_url_round_trips_non_ascii(self):
        q = parse_qs(urlsplit(g.explore_url('fußball', 'US', 'today 12-m')).query)
        self.assertEqual(q['hl'], ['en-US'])
        req = json.loads(q['req'][0])
        self.assertEqual(req['comparisonItem'], [{'keyword': 'fußball', 'geo': 'US', 'time': 'today 12-m'}])
        self.assertIn('%C3%9F', g.explore_url('fußball', '', 'today 12-m'))   # raw UTF-8, like the browser

    def test_widgets_and_widget_url(self):
        w = g.explore_widgets(EXPLORE)
        self.assertEqual(set(w), {'TIMESERIES', 'GEO_MAP', 'RELATED_TOPICS', 'RELATED_QUERIES'})
        q = parse_qs(urlsplit(g.widget_url('multiline', w['TIMESERIES'])).query)
        self.assertEqual(q['token'], ['T1'])
        self.assertEqual(json.loads(q['req'][0]), TS_REQ)

    def test_explore_without_timeseries_is_an_error(self):
        with self.assertRaises(ValueError):
            g.explore_widgets(b")]}'\n{\"widgets\": []}")
        with self.assertRaises(ValueError):
            g.explore_widgets(b'<html>Error 429 (Too Many Requests)</html>')

    def test_parsers(self):
        self.assertEqual(g.multiline_points(MULTILINE), 53)
        self.assertEqual(g.related_count(RELATED), 4)
        self.assertEqual(g.comparedgeo_count(b")]}',\n{\"default\":{\"geoMapData\":[{},{}]}}"), 2)
        self.assertEqual(g.autocomplete_count(b")]}',\n{\"default\":{\"topics\":[{},{},{}]}}"), 3)
        self.assertEqual(g.rss_items(RSS), 2)
        self.assertIn('/autocomplete/taylor%20swift?', g.autocomplete_url('taylor swift'))
        self.assertTrue(g.rss_url('').endswith('geo=US'))


def row(endpoint, status, ok, throttled=False, latency=100, seq=None, google=True, count=None):
    return {'endpoint': endpoint, 'status': status, 'ok': ok, 'throttled': throttled, 'latencyMs': latency,
            'bytes': 10, 'google': google, 'googleSeq': seq, 'keyword': 'k', 'keywordIndex': 0, 'attempt': 1,
            'sessionNo': 1, 'elapsedSecs': 1.0, 'count': count}


class StatsTests(unittest.TestCase):
    def test_p90(self):
        self.assertEqual(stats.p90(list(range(1, 11))), 9)
        self.assertEqual(stats.p90([5]), 5)
        self.assertIsNone(stats.p90([]))

    def test_block(self):
        rows = [row('explore', 200, True, seq=1), row('explore', 429, False, True, seq=2),
                row('explore', None, False, latency=10000, seq=3), row('explore', 429, False, True, seq=4)]
        b = stats.block(rows)
        self.assertEqual((b['requests'], b['ok'], b['count429'], b['countThrottled'], b['countOther']), (4, 1, 2, 2, 1))
        self.assertEqual(b['successRate'], 0.25)
        self.assertEqual(b['first429']['googleSeq'], 2)
        self.assertEqual(b['statusCounts'], {'200': 1, '429': 2, 'none': 1})
        self.assertEqual(b['latencyMsMedian'], 100)          # the timeout is not a latency

    def test_keywords_and_points(self):
        k = stats.keyword_block([
            {'keyword': 'a', 'index': 0, 'attempts': 1, 'throttled': False, 'ok': True, 'points': 53},
            {'keyword': 'b', 'index': 1, 'attempts': 2, 'throttled': True, 'ok': True, 'points': 53},
            {'keyword': 'c', 'index': 2, 'attempts': 3, 'throttled': True, 'ok': False, 'points': None}])
        self.assertEqual((k['with429'], k['recoveredAfterRetry'], k['failedAfterRetries'], k['retriesUsed']),
                         (2, 1, 1, 3))
        self.assertTrue(k['retriesRecovered'])
        self.assertEqual(k['firstThrottledKeywordIndex'], 1)
        p = stats.points_block([row('multiline', 200, True, count=53), row('multiline', 200, True, count=0)])
        self.assertEqual((p['keywordsWithPoints'], p['min'], p['max']), (1, 0, 53))


class InputTests(unittest.TestCase):
    def test_schema_defaults_match_code(self):
        schema = json.loads((ROOT / '.actor' / 'INPUT_SCHEMA.json').read_text())['properties']
        self.assertEqual(set(schema), set(m.DEFAULTS))
        for k, v in m.DEFAULTS.items():
            self.assertEqual(schema[k]['default'], v, k)
        for k, (lo, hi) in m.INT_RANGES.items():
            self.assertEqual((schema[k]['minimum'], schema[k]['maximum']), (lo, hi), k)
        self.assertEqual(schema['client']['enum'], list(m.CLIENTS))

    def test_read_input(self):
        cfg = m.read_input({})
        self.assertEqual(len(cfg['keywords']), 30)
        cfg = m.read_input({'keywords': [' a ', 'a', '', 'b'], 'geo': 'us', 'delayMs': '300'})
        self.assertEqual((cfg['keywords'], cfg['geo'], cfg['delayMs']), (['a', 'b'], 'US', 300))
        for bad in ({'client': 'requests'}, {'delayMs': -1}, {'retry429': 'x'}, {'keywords': 'a'}, {'warmup': True}):
            with self.assertRaises(ValueError):
                m.read_input(bad)

    def test_no_credentials_leak(self):
        pc = {'useApifyProxy': False, 'proxyUrls': ['http://user:s3cret@1.2.3.4:8000']}
        self.assertNotIn('s3cret', json.dumps(m.echo_input(m.read_input({'proxyConfiguration': pc}))))
        s = Session('httpx', 'http://groups-X,session-a:s3cret@proxy.apify.com:8000', 'chrome')
        self.assertNotIn('s3cret', s.scrub('ProxyError: http://groups-X:s3cret@proxy.apify.com:8000 and s3cret'))
        self.assertEqual(m.describe_proxy({'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL'],
                                           'apifyProxyCountry': 'US'}), 'apify:RESIDENTIAL country=US')


class FakeSession:
    """Answers by URL; explore answers 429 for the first `explore_429` calls across all sessions."""
    opened = 0
    explore_429 = 0

    def __init__(self, kind, proxy_url, impersonate):
        FakeSession.opened += 1

    def has_cookie(self, name):
        return True

    def scrub(self, text):
        return text

    async def aclose(self):
        pass

    async def get(self, url, headers=None, follow=False):
        if 'ipify' in url:
            return Resp(200, 5, b'{"ip": "203.0.113.9"}')
        if 'ipinfo' in url:
            return Resp(200, 5, b'{"ip": "203.0.113.9", "org": "AS64500 Example", "country": "US"}')
        if 'peet' in url:
            return Resp(200, 5, b'{"ja4": "t13d"}')
        if '/api/explore' in url:
            if FakeSession.explore_429 > 0:
                FakeSession.explore_429 -= 1
                return Resp(429, 50, b'<html>Error 429 (Too Many Requests)!!1</html>')
            return Resp(200, 50, EXPLORE)
        if '/multiline' in url:
            return Resp(200, 50, MULTILINE)
        if '/trending/rss' in url:
            return Resp(200, 50, RSS)
        if '/autocomplete/' in url:
            return Resp(200, 50, b")]}',\n{\"default\":{\"topics\":[{}]}}")
        return Resp(200, 50, b'<html></html>')                       # warmup


class RetryLoopTests(unittest.TestCase):
    def run_probe(self, explore_429, **inp):
        FakeSession.opened, FakeSession.explore_429 = 0, explore_429
        cfg = m.read_input({'keywords': ['a', 'b', 'c'], 'delayMs': 0, 'retryBackoffSecs': 0, **inp})
        probe = m.Probe(cfg, None)
        with mock.patch.object(m, 'Session', FakeSession), \
                mock.patch.object(m, 'seconds_left_in_run', lambda: None), \
                mock.patch.object(m.Probe, 'flush', mock.AsyncMock()), \
                mock.patch.object(m.Probe, 'report', mock.AsyncMock()):
            asyncio.run(probe.run())
        return probe, probe.output('done')

    def test_clean_run(self):
        probe, out = self.run_probe(0, newSessionEvery=2)
        self.assertEqual(out['keywords']['succeeded'], 3)
        self.assertEqual(out['multilinePoints']['min'], 53)
        self.assertEqual(len(out['sessions']), 2)                     # keyword index 2 starts session 2
        self.assertEqual(out['rss'], {'status': 200, 'items': 2})
        # warmup x2, rss, autocomplete, 3 x (explore + multiline)
        self.assertEqual(out['googleRequests'], 10)
        self.assertEqual(out['sessions'][0]['org'], 'AS64500 Example')

    def test_429_recovers_after_rotation(self):
        probe, out = self.run_probe(1, newSessionEvery=0)
        k = out['keywords']
        self.assertEqual((k['succeeded'], k['with429'], k['recoveredAfterRetry'], k['retriesUsed']), (3, 1, 1, 1))
        self.assertEqual(len(out['sessions']), 2)                     # rotateOn429 opened a second session
        self.assertEqual(out['perEndpoint']['explore']['first429']['googleSeq'], 2)   # warmup, then explore

    def test_429_exhausts_retries_without_rotation(self):
        probe, out = self.run_probe(10, newSessionEvery=0, retry429=1, rotateOn429=False)
        k = out['keywords']
        self.assertEqual((k['succeeded'], k['with429'], k['failedAfterRetries']), (0, 3, 3))
        self.assertEqual(len(out['sessions']), 1)
        self.assertEqual(out['perEndpoint']['explore']['count429'], 6)
        self.assertNotIn('multiline', out['perEndpoint'])

    def test_no_warmup(self):
        probe, out = self.run_probe(0, newSessionEvery=2, warmup='none')
        self.assertEqual(out['googleRequests'], 8)                    # rss, autocomplete, 3 x 2
        self.assertNotIn('warmup', out['perEndpoint'])
        self.assertEqual([s['warmupStatus'] for s in out['sessions']], [None, None])
        self.assertTrue(all(len(r['bodySha1']) == 12 for r in probe.rows if r['google']))

    def test_rss_warmup_and_extras_last(self):
        probe, out = self.run_probe(0, warmup='rss', newSessionEvery=0)
        order = [r['endpoint'] for r in probe.rows if r['google']]
        self.assertEqual(order[0], 'warmup')
        self.assertEqual(order[-2:], ['rss', 'autocomplete'])        # never before explore

    def test_budget_stops_the_run(self):
        probe, out = self.run_probe(0, maxGoogleRequests=5)
        self.assertEqual((out['googleRequests'], out['stopReason']), (5, 'maxGoogleRequests'))


if __name__ == '__main__':
    unittest.main()
