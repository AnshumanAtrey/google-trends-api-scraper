"""The parsers against Google's real answers, saved on 2026-09-24 (tests/fixtures/).

Run: python -m unittest discover -s tests -t .

The batchexecute fixture is Google's real Trending Now answer for the US, past 24 hours
(216 trends, 281 KB) with the list trimmed to 7 of its items; the envelope is unchanged.
"""
import json
import unittest
from pathlib import Path

from src import parse
from src.inputs import parse_input

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
ACTOR_JSON = Path(__file__).resolve().parent.parent / '.actor' / 'actor.json'
KEYWORD_KEYS = ['keyword', 'keywordType', 'comparedWith', 'geo', 'geoName', 'timeRange', 'startDate', 'endDate', 'step',
                'category', 'property', 'interestOverTime', 'averageInterest', 'regionLevel', 'interestByRegion',
                'relatedQueries', 'missing', 'trendsUrl', 'scrapedAt']
TREND_KEYS = ['term', 'geo', 'trendingHours', 'isActive', 'startedAt', 'endedAt', 'searchVolume', 'increasePercent',
              'categories', 'relatedSearches', 'news', 'scrapedAt']


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Guards(unittest.TestCase):
    def test_both_xssi_guards(self):
        self.assertIn('widgets', parse.api_json(load('explore_bitcoin_12m.txt')))          # )]}'\n
        self.assertIn('default', parse.api_json(load('multiline_bitcoin_12m.txt')))       # )]}',\n

    def test_throttle_page_is_not_json(self):
        with self.assertRaises(ValueError):
            parse.explore_widgets(load('explore_429_nocookie.html'))
        with self.assertRaises(ValueError):
            parse.api_json(load('multiline_401_tampered.html'))


class Explore(unittest.TestCase):
    def test_one_keyword(self):
        w = parse.explore_widgets(load('explore_bitcoin_12m.txt'))
        self.assertEqual(list(w), ['TIMESERIES', 'GEO_MAP', 'RELATED_TOPICS', 'RELATED_QUERIES'])
        self.assertEqual(parse.period(w), {'startDate': '2025-09-24', 'endDate': '2026-09-24', 'step': 'week',
                                           'intraday': False})
        self.assertEqual(parse.related_widget_id(0, 1), 'RELATED_QUERIES')

    def test_comparison(self):
        w = parse.explore_widgets(load('explore_5coins_US_7d_news.txt'))
        self.assertIn('GEO_MAP', w)
        self.assertEqual(w['GEO_MAP']['type'], 'fe_multi_heat_map')
        for i in range(5):
            self.assertIn(parse.related_widget_id(i, 5), w)
        self.assertEqual(parse.period(w), {'startDate': '2026-09-17', 'endDate': '2026-09-24', 'step': 'hour',
                                           'intraday': True})

    def test_steps(self):
        hour = parse.period(parse.explore_widgets(load('explore_minecraft_IN_1H_youtube.txt')))
        self.assertEqual((hour['step'], hour['intraday'], hour['startDate']), ('minute', True, '2026-09-24'))
        custom = parse.period(parse.explore_widgets(load('explore_bitcoin_GB_custom_cat7_images.txt')))
        self.assertEqual((custom['startDate'], custom['endDate'], custom['step']), ('2024-01-01', '2024-06-30', 'day'))

    def test_region_level_edit_touches_only_resolution(self):
        geo = parse.explore_widgets(load('explore_bitcoin_12m.txt'))['GEO_MAP']
        self.assertIs(parse.with_resolution(geo, None), geo)
        city = parse.with_resolution(geo, 'CITY')
        self.assertEqual(city['request']['resolution'], 'CITY')
        self.assertEqual(geo['request']['resolution'], 'COUNTRY')
        self.assertEqual({k: v for k, v in city['request'].items() if k != 'resolution'},
                         {k: v for k, v in geo['request'].items() if k != 'resolution'})
        self.assertEqual(city['token'], geo['token'])


class Widgets(unittest.TestCase):
    def test_timeline_one_keyword(self):
        series, has, avg = parse.timeline(load('multiline_bitcoin_12m.txt'), 1, intraday=False)
        self.assertEqual(len(series[0]), 53)
        self.assertEqual(series[0][0], {'date': '2025-09-21', 'value': 40, 'isPartial': False})
        self.assertEqual(series[0][-1], {'date': '2026-09-20', 'value': 34, 'isPartial': True})
        self.assertEqual(max(p['value'] for p in series[0]), 100)
        self.assertEqual((has, avg), ([True], [None]))

    def test_timeline_comparison(self):
        series, has, avg = parse.timeline(load('multiline_5coins_US_7d_news.txt'), 5, intraday=True)
        self.assertEqual([len(s) for s in series], [169] * 5)
        self.assertEqual(series[1][0], {'date': '2026-09-17T06:00:00+00:00', 'value': 4, 'isPartial': False})
        self.assertEqual(avg, [28, 8, 7, 0, 9])
        self.assertEqual(has, [True] * 5)

    def test_timeline_minutes(self):
        series, _, _ = parse.timeline(load('multiline_minecraft_IN_1H_youtube.txt'), 1, intraday=True)
        self.assertEqual(series[0][0]['date'], '2026-09-24T05:34:00+00:00')

    def test_regions_drop_no_data_and_sort(self):
        rows = parse.regions(load('comparedgeo_bitcoin_12m.txt'), 1)[0]
        self.assertEqual(len(rows), 64)                      # of 250 countries, 64 have data
        self.assertEqual(rows[0], {'regionName': 'Switzerland', 'regionCode': 'CH', 'value': 100})
        self.assertEqual([r['value'] for r in rows], sorted((r['value'] for r in rows), reverse=True))

    def test_regions_comparison_and_cities(self):
        self.assertEqual([len(r) for r in parse.regions(load('comparedgeo_5coins_US_7d_news.txt'), 5)],
                         [50, 40, 41, 12, 40])
        city = parse.regions(load('comparedgeo_bitcoin_GB_city.txt'), 1)[0]
        self.assertEqual(city[0], {'regionName': 'Shifnal', 'regionCode': None, 'value': 100})

    def test_related_queries(self):
        rq = parse.related(load('related_bitcoin_12m.txt'))
        self.assertEqual((len(rq['top']), len(rq['rising'])), (25, 25))
        self.assertEqual(rq['top'][0], {'query': 'bitcoin price', 'value': 100})
        self.assertEqual(rq['rising'][0], {'query': 'how to buy bitcoin safely', 'value': 18750, 'growth': 'Breakout'})
        self.assertIn({'query': 'clarity act', 'value': 2650, 'growth': '+2,650%'}, rq['rising'])

    def test_related_empty(self):
        self.assertEqual(parse.related(load('related_empty.txt')), {'top': [], 'rising': []})

    def test_autocomplete(self):
        topics = parse.autocomplete(load('autocomplete_bitcoin.txt'))
        self.assertEqual(len(topics), 5)
        self.assertEqual(topics[0], {'title': 'Bitcoin', 'type': 'Cryptocurrency', 'topicId': '/m/05p0rrx'})


class Trending(unittest.TestCase):
    def setUp(self):
        self.items = parse.batch_items(load('batchexecute_trending_US_24h_trimmed.txt'))

    def test_items(self):
        self.assertEqual(len(self.items), 7)
        self.assertEqual(self.items[0][0], 'jonathan taylor thomas')

    def test_active_trend(self):
        row = parse.trend_row(self.items[3], 'US', 24, 3, 'T')
        self.assertEqual(list(row), TREND_KEYS)
        self.assertEqual((row['term'], row['isActive'], row['endedAt'], row['categories']),
                         ('meta vr glasses', True, None, ['Technology']))
        self.assertIsInstance(row['searchVolume'], int)
        self.assertIsInstance(row['increasePercent'], int)
        self.assertTrue(row['startedAt'].endswith('+00:00'))
        self.assertEqual(len(row['news']), 3)
        self.assertEqual(set(row['news'][0]), {'title', 'url', 'source', 'publishedAt', 'imageUrl'})
        self.assertTrue(row['news'][0]['publishedAt'].endswith('+00:00'))

    def test_ended_trend_and_news_limit(self):
        ended = next(i for i in self.items if i[4] is not None and i[1])
        row = parse.trend_row(ended, 'US', 24, 1, 'T')
        self.assertFalse(row['isActive'])
        self.assertTrue(row['endedAt'].endswith('+00:00'))
        self.assertEqual(len(row['news']), 1)
        no_news = next(i for i in self.items if i[1] is None)
        self.assertEqual(parse.trend_row(no_news, 'US', 24, 3, 'T')['news'], [])

    def test_topic_filter(self):
        tech = [i[0] for i in self.items if parse.has_topic(i, 18)]
        self.assertEqual(tech, ['meta vr glasses', 'xbox'])
        self.assertEqual(len([i for i in self.items if parse.has_topic(i, None)]), 7)

    def test_error_answer(self):
        body = ')]}\'\n\n60\n[["wrb.fr","i0OFE",null,null,null,[3],"generic"]]\n'
        with self.assertRaisesRegex(ValueError, 'error'):
            parse.batch_items(body)
        with self.assertRaises(ValueError):
            parse.batch_items(load('explore_429_nocookie.html'))

    def test_rss_fallback(self):
        rows = parse.rss_rows(load('rss_trending_US.xml'), 'US', 2, 'T')
        self.assertEqual(len(rows), 10)
        self.assertEqual(list(rows[0]), TREND_KEYS)
        self.assertEqual((rows[0]['term'], rows[0]['searchVolume'], rows[0]['startedAt']),
                         ('uss abraham lincoln', 10000, '2026-09-24T05:50:00+00:00'))
        self.assertEqual(len(rows[0]['news']), 2)
        self.assertIsNone(rows[0]['trendingHours'])


class Rows(unittest.TestCase):
    def test_keyword_row_keys_and_url(self):
        cfg = parse_input({'mode': 'compare', 'searchTerms': ['bitcoin', 'ethereum'], 'geo': 'US',
                           'timeRange': 'now 7-d', 'property': 'news', 'category': 7})
        row = parse.keyword_row('ethereum', cfg.keywords, cfg, {'startDate': 'a', 'endDate': 'b', 'step': 'hour'}, 'T',
                                points=None, average=8, region_level='region', region_rows=None,
                                related_queries=None, missing=['interestByRegion'])
        self.assertEqual(list(row), KEYWORD_KEYS)
        self.assertEqual((row['comparedWith'], row['averageInterest'], row['geoName']),
                         (['bitcoin'], 8, 'United States'))
        self.assertEqual((row['interestOverTime'], row['interestByRegion'], row['relatedQueries']),
                         ([], [], {'top': [], 'rising': []}))
        self.assertEqual(row['trendsUrl'], 'https://trends.google.com/trends/explore?q=bitcoin,ethereum&date=now%207-d'
                                           '&geo=US&cat=7&gprop=news')

    def test_keywords_mode_has_no_average(self):
        cfg = parse_input({'searchTerms': ['bitcoin']})
        row = parse.keyword_row('bitcoin', cfg.keywords, cfg, {}, 'T', points=[], average=5, region_level=None,
                                region_rows=[], related_queries=None, missing=[])
        self.assertIsNone(row['averageInterest'])
        self.assertEqual((row['comparedWith'], row['geoName']), ([], 'Worldwide'))

    def test_rows_use_only_declared_dataset_fields(self):
        fields = set(json.loads(ACTOR_JSON.read_text())['storages']['dataset']['fields']['properties'])
        self.assertLessEqual(set(KEYWORD_KEYS), fields)
        self.assertLessEqual(set(TREND_KEYS), fields)
        self.assertLessEqual({'keyword', 'suggestions', 'scrapedAt'}, fields)


if __name__ == '__main__':
    unittest.main()
