"""Input validation: defaults, normalisation and the plain error messages.

Run: python -m unittest discover -s tests -t .
"""
import unittest
from datetime import date

from src.inputs import InputError, keyword_type, parse_input

TODAY = date(2026, 9, 24)


def parse(raw: dict):
    return parse_input(raw, today=TODAY)


class Defaults(unittest.TestCase):
    def test_example_input(self):
        cfg = parse({'mode': 'keywords', 'searchTerms': ['bitcoin', 'chatgpt'], 'timeRange': 'today 12-m'})
        self.assertEqual(cfg.mode, 'keywords')
        self.assertEqual(cfg.keywords, ['bitcoin', 'chatgpt'])
        self.assertEqual(cfg.geo, '')
        self.assertEqual(cfg.data_types, ('interestOverTime', 'interestByRegion', 'relatedQueries'))
        self.assertEqual((cfg.search_property, cfg.google_property, cfg.category), ('web', '', 0))
        self.assertIsNone(cfg.google_resolution)

    def test_only_keywords(self):
        cfg = parse({'searchTerms': ['bitcoin']})
        self.assertEqual((cfg.mode, cfg.time_range), ('keywords', 'today 12-m'))

    def test_trending_needs_no_keywords_and_defaults_to_us(self):
        cfg = parse({'mode': 'trending'})
        self.assertEqual((cfg.geo, cfg.trending_hours, cfg.news_per_trend, cfg.max_items), ('US', 24, 3, 0))
        self.assertIsNone(cfg.trending_category_id)


class Keywords(unittest.TestCase):
    def test_no_keywords_fails_plainly(self):
        for raw in ({}, {'searchTerms': []}, {'searchTerms': ['  ', '']}, {'mode': 'suggestions'}):
            with self.assertRaisesRegex(InputError, 'No keywords to look up'):
                parse(raw)

    def test_blank_lines_repeats_and_spaces(self):
        cfg = parse({'searchTerms': ['  bitcoin ', 'bitcoin', '', 'taylor   swift']})
        self.assertEqual(cfg.keywords, ['bitcoin', 'taylor swift'])

    def test_text_with_newlines(self):
        self.assertEqual(parse({'searchTerms': 'bitcoin\nchatgpt\n'}).keywords, ['bitcoin', 'chatgpt'])

    def test_overlong_keyword_is_reported_not_fatal(self):
        cfg = parse({'searchTerms': ['bitcoin', 'x' * 150]})
        self.assertEqual(cfg.keywords, ['bitcoin'])
        self.assertEqual(len(cfg.invalid), 1)
        self.assertIn('100 characters', cfg.invalid[0][1])

    def test_compare_limits(self):
        with self.assertRaisesRegex(InputError, "2 to 5 keywords.*Google's own limit.*you entered 6"):
            parse({'mode': 'compare', 'searchTerms': list('abcdef')})
        with self.assertRaisesRegex(InputError, 'you entered 1'):
            parse({'mode': 'compare', 'searchTerms': ['bitcoin']})
        self.assertEqual(len(parse({'mode': 'compare', 'searchTerms': list('abcde')}).keywords), 5)

    def test_topic_id(self):
        self.assertEqual(keyword_type('/m/05p0rrx'), 'Topic')
        self.assertEqual(keyword_type('/g/11wxkqw4vk'), 'Topic')
        self.assertEqual(keyword_type('bitcoin'), 'Search term')


class Geo(unittest.TestCase):
    def test_normalised(self):
        self.assertEqual(parse({'searchTerms': ['a'], 'geo': ' us '}).geo, 'US')
        self.assertEqual(parse({'searchTerms': ['a'], 'geo': 'UK'}).geo, 'GB')
        self.assertEqual(parse({'searchTerms': ['a'], 'geo': 'Worldwide'}).geo, '')

    def test_refused(self):
        with self.assertRaisesRegex(InputError, 'state or city'):
            parse({'searchTerms': ['a'], 'geo': 'US-CA'})
        with self.assertRaisesRegex(InputError, 'not a Google Trends country code'):
            parse({'searchTerms': ['a'], 'geo': 'XX'})


class TimeRange(unittest.TestCase):
    def test_codes_any_case(self):
        self.assertEqual(parse({'searchTerms': ['a'], 'timeRange': 'Today 12-M'}).time_range, 'today 12-m')
        self.assertEqual(parse({'searchTerms': ['a'], 'timeRange': 'now 1-h'}).time_range, 'now 1-H')
        self.assertEqual(parse({'searchTerms': ['a'], 'timeRange': 'ALL'}).time_range, 'all')

    def test_custom_dates(self):
        self.assertEqual(parse({'searchTerms': ['a'], 'timeRange': '2024-01-01  2024-06-30'}).time_range,
                         '2024-01-01 2024-06-30')
        self.assertEqual(parse({'searchTerms': ['a'], 'timeRange': '2024-01-01 to 2024-06-30'}).time_range,
                         '2024-01-01 2024-06-30')

    def test_bad_dates(self):
        for value, message in (('2024-06-30 2024-01-01', 'first date is after'),
                               ('2003-01-01 2004-06-30', 'starts on 2004-01-01'),
                               ('2026-01-01 2026-12-31', 'in the future'),
                               ('last week', 'not one Google understands'),
                               ('2024-13-01 2024-12-01', 'not one Google understands')):
            with self.subTest(value=value), self.assertRaisesRegex(InputError, message):
                parse({'searchTerms': ['a'], 'timeRange': value})


class Options(unittest.TestCase):
    def test_data_types(self):
        self.assertEqual(parse({'searchTerms': ['a'], 'dataTypes': ['relatedqueries', 'interestOverTime']}).data_types,
                         ('interestOverTime', 'relatedQueries'))
        with self.assertRaisesRegex(InputError, 'is empty'):
            parse({'searchTerms': ['a'], 'dataTypes': []})
        with self.assertRaisesRegex(InputError, 'relatedTopics'):
            parse({'searchTerms': ['a'], 'dataTypes': ['relatedTopics']})

    def test_property_and_category(self):
        cfg = parse({'searchTerms': ['a'], 'property': 'Shopping', 'category': '7'})
        self.assertEqual((cfg.search_property, cfg.google_property, cfg.category), ('froogle', 'froogle', 7))
        with self.assertRaisesRegex(InputError, 'Subject area'):
            parse({'searchTerms': ['a'], 'category': -1})
        with self.assertRaisesRegex(InputError, 'Search type'):
            parse({'searchTerms': ['a'], 'property': 'maps'})

    def test_region_level(self):
        self.assertEqual(parse({'searchTerms': ['a'], 'regionLevel': 'city'}).google_resolution, 'CITY')
        with self.assertRaisesRegex(InputError, 'Region detail'):
            parse({'searchTerms': ['a'], 'regionLevel': 'dma'})

    def test_trending_options(self):
        cfg = parse({'mode': 'trending', 'trendingHours': 168, 'trendingCategory': '18', 'newsPerTrend': 0,
                     'maxItems': 50, 'geo': 'in'})
        self.assertEqual((cfg.trending_hours, cfg.trending_category, cfg.trending_category_id, cfg.news_per_trend,
                          cfg.max_items, cfg.geo), (168, 'technology', 18, 0, 50, 'IN'))
        self.assertEqual(parse({'mode': 'trending', 'trendingHours': '4'}).trending_hours, 4)
        with self.assertRaisesRegex(InputError, '4, 24, 48 or 168'):
            parse({'mode': 'trending', 'trendingHours': 12})
        with self.assertRaisesRegex(InputError, 'from 0 to 10'):
            parse({'mode': 'trending', 'newsPerTrend': 11})
        with self.assertRaisesRegex(InputError, 'Trend topic'):
            parse({'mode': 'trending', 'trendingCategory': 'crypto'})

    def test_mode(self):
        self.assertEqual(parse({'mode': 'COMPARE', 'searchTerms': ['a', 'b']}).mode, 'compare')
        with self.assertRaisesRegex(InputError, 'Report type'):
            parse({'mode': 'explore', 'searchTerms': ['a']})


if __name__ == '__main__':
    unittest.main()
