"""
Input validation: turn the raw run input into a Config, or raise InputError with a plain sentence.

Every field is optional except the keywords (not needed for Trending Now). Values are
normalised the way people type them: geo "us" or "UK" becomes US or GB, "Today 12-M" becomes
"today 12-m", trendingHours may be 24 or "24". Anything that cannot be used fails the run
with a message that says what to type instead, before a single request goes to Google.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

MODES = ('keywords', 'compare', 'trending', 'suggestions')
TIME_CODES = ('now 1-H', 'now 4-H', 'now 1-d', 'now 7-d', 'today 1-m', 'today 3-m', 'today 12-m', 'today 5-y', 'all')
DATA_TYPES = ('interestOverTime', 'interestByRegion', 'relatedQueries')
PROPERTIES = {'web': '', 'news': 'news', 'images': 'images', 'youtube': 'youtube', 'froogle': 'froogle'}
PROPERTY_ALIASES = {'': 'web', 'shopping': 'froogle', 'image': 'images'}
REGION_LEVELS = {'auto': None, 'country': 'COUNTRY', 'region': 'REGION', 'city': 'CITY'}
TRENDING_HOURS = (4, 24, 48, 168)
# Google's Trending Now topics (the data-value menu on trends.google.com/trending, 2026-09-24).
# A different list from the explore category numbers. 12 is not used by Google.
TRENDING_CATEGORIES = {
    'autos': 1, 'beauty': 2, 'business': 3, 'entertainment': 4, 'food': 5, 'games': 6, 'health': 7,
    'hobbies': 8, 'jobs': 9, 'law': 10, 'other': 11, 'pets': 13, 'politics': 14, 'science': 15,
    'shopping': 16, 'sports': 17, 'technology': 18, 'travel': 19, 'climate': 20,
}
GEO_ALIASES = {'UK': 'GB', 'WW': '', 'WORLDWIDE': '', 'GLOBAL': '', 'WORLD': ''}
COMPARE_MIN, COMPARE_MAX = 2, 5             # Google's own limit per comparison
MAX_KEYWORD_CHARS = 100                     # our guard: a longer "keyword" is almost always pasted text
MAX_NEWS = 10
FIRST_DAY = date(2004, 1, 1)                # Google Trends data starts here
COUNTRIES: dict[str, str] = json.loads((Path(__file__).parent / 'countries.json').read_text(encoding='utf-8'))
TOPIC_ID = re.compile(r'^/[mg]/[0-9a-z_]+$')


class InputError(ValueError):
    """The input cannot be used. The message is shown to the user as the run's status."""


@dataclass
class Config:
    mode: str = 'keywords'
    keywords: list[str] = field(default_factory=list)       # usable keywords, in the order typed
    invalid: list[tuple[str, str]] = field(default_factory=list)   # (keyword as typed, why it was refused)
    geo: str = ''
    time_range: str = 'today 12-m'
    data_types: tuple[str, ...] = DATA_TYPES
    search_property: str = 'web'                             # the input value (property); see google_property
    category: int = 0
    region_level: str = 'auto'
    trending_hours: int = 24
    trending_category: str = 'all'
    news_per_trend: int = 3
    max_items: int = 0

    def wants(self, part: str) -> bool:
        return part in self.data_types

    @property
    def google_property(self) -> str:
        return PROPERTIES[self.search_property]

    @property
    def google_resolution(self) -> str | None:
        return REGION_LEVELS[self.region_level]

    @property
    def trending_category_id(self) -> int | None:
        return TRENDING_CATEGORIES.get(self.trending_category)


def keyword_type(keyword: str) -> str:
    """Topic for a Google topic id such as /m/05p0rrx, Search term for anything else."""
    return 'Topic' if TOPIC_ID.match(keyword) else 'Search term'


def geo_name(geo: str) -> str:
    return COUNTRIES.get(geo, geo) if geo else 'Worldwide'


def _choice(raw: dict, key: str, allowed, default, *, aliases: dict | None = None, label: str) -> str:
    value = raw.get(key)
    if value is None:
        return default
    text = str(value).strip().lower()
    text = (aliases or {}).get(text, text)
    if text not in allowed:
        raise InputError(f'{label} ({key}) must be one of {", ".join(allowed)}, not "{value}".')
    return text


def _int(raw: dict, key: str, default: int, lo: int, hi: int | None, label: str) -> int:
    value = raw.get(key)
    if value is None or value == '':
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise InputError(f'{label} ({key}) must be a whole number, not "{value}".') from None
    if number < lo or (hi is not None and number > hi):
        span = f'from {lo} to {hi}' if hi is not None else f'{lo} or more'
        raise InputError(f'{label} ({key}) must be {span}, not {number}.')
    return number


def parse_keywords(value) -> tuple[list[str], list[tuple[str, str]]]:
    """(usable keywords, refused ones with the reason). Blank lines and repeats are dropped."""
    if value is None:
        return [], []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise InputError('Keywords (searchTerms) must be a list of search terms, one per line.')
    good: list[str] = []
    bad: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, (dict, list)) or item is None:
            bad.append((json.dumps(item)[:60], 'This is not a search term.'))
            continue
        text = ' '.join(str(item).split())
        if not text:
            continue
        if len(text) > MAX_KEYWORD_CHARS:
            bad.append((text[:60] + '...',
                        f'Longer than {MAX_KEYWORD_CHARS} characters; enter a search term, not a sentence.'))
            continue
        if text not in good:
            good.append(text)
    return good, bad


def parse_geo(value, *, default: str = '') -> str:
    text = str(value or '').strip().upper()
    text = GEO_ALIASES.get(text, text)
    if not text:
        return default
    if '-' in text:
        raise InputError(f'Country (geo) "{value}" looks like a state or city code. Only whole countries are '
                         f'supported: enter a two-letter code such as US, or leave it empty for worldwide.')
    if text not in COUNTRIES:
        raise InputError(f'Country (geo) "{value}" is not a Google Trends country code. Enter two letters such as '
                         f'US, GB or IN, or leave it empty for worldwide.')
    return text


def parse_time_range(value, today: date | None = None) -> str:
    """One of Google's codes, or 'YYYY-MM-DD YYYY-MM-DD' checked against 2004-01-01 and today."""
    if value is None or not str(value).strip():
        return 'today 12-m'
    text = ' '.join(str(value).split())
    for code in TIME_CODES:
        if text.lower() == code.lower():
            return code
    parts = text.replace(' to ', ' ').replace(',', ' ').split()
    if len(parts) == 2:
        try:
            start, end = (date.fromisoformat(p) for p in parts)
        except ValueError:
            start = end = None
        if start and end:
            today = today or datetime.now(timezone.utc).date()
            if start > end:
                raise InputError(f'Time period (timeRange) "{value}": the first date is after the second.')
            if start < FIRST_DAY:
                raise InputError(f'Time period (timeRange) "{value}": Google Trends data starts on 2004-01-01.')
            if end > today:
                raise InputError(f'Time period (timeRange) "{value}": the second date is in the future '
                                 f'(today is {today.isoformat()} in UTC).')
            return f'{start.isoformat()} {end.isoformat()}'
    raise InputError(f'Time period (timeRange) "{value}" is not one Google understands. Pick one from the list, such '
                     f'as today 12-m, or type two dates like 2024-01-01 2024-06-30.')


def parse_data_types(value) -> tuple[str, ...]:
    if value is None:
        return DATA_TYPES
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise InputError(f'Data to fetch (dataTypes) must be a list with some of {", ".join(DATA_TYPES)}.')
    lowered = {d.lower(): d for d in DATA_TYPES}
    chosen = []
    for item in value:
        name = lowered.get(str(item).strip().lower())
        if not name:
            raise InputError(f'Data to fetch (dataTypes): "{item}" is not one of {", ".join(DATA_TYPES)}.')
        chosen.append(name)
    if not chosen:
        raise InputError('Data to fetch (dataTypes) is empty. Choose at least one of interest over time, interest '
                         'by region and related queries.')
    return tuple(d for d in DATA_TYPES if d in chosen)


def parse_input(raw: dict | None, today: date | None = None) -> Config:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise InputError('The input must be a JSON object, as the input form produces.')
    mode = _choice(raw, 'mode', MODES, 'keywords', label='Report type')
    cfg = Config(mode=mode)
    cfg.keywords, cfg.invalid = parse_keywords(raw.get('searchTerms'))

    if mode != 'trending':
        if not cfg.keywords:
            why = f' {cfg.invalid[0][1]}' if cfg.invalid else ''
            raise InputError(f'No keywords to look up.{why} Enter at least one keyword in Keywords (searchTerms), '
                             f'for example bitcoin, one per line.')
        if mode == 'compare' and cfg.invalid:
            raise InputError(f'Compare mode needs every keyword to be usable. "{cfg.invalid[0][0]}": '
                             f'{cfg.invalid[0][1]}')
        if mode == 'compare' and not COMPARE_MIN <= len(cfg.keywords) <= COMPARE_MAX:
            raise InputError(f'Compare mode puts {COMPARE_MIN} to {COMPARE_MAX} keywords on one scale, which is '
                             f'Google\'s own limit; you entered {len(cfg.keywords)}. '
                             + ('Remove some, or switch Report type (mode) to keywords to get each one on its own '
                                'scale.' if len(cfg.keywords) > COMPARE_MAX else
                                'Add another keyword, or switch Report type (mode) to keywords.'))

    cfg.geo = parse_geo(raw.get('geo'), default='US' if mode == 'trending' else '')
    cfg.time_range = parse_time_range(raw.get('timeRange'), today)
    cfg.data_types = parse_data_types(raw.get('dataTypes'))
    cfg.search_property = _choice(raw, 'property', tuple(PROPERTIES), 'web', aliases=PROPERTY_ALIASES,
                                  label='Search type')
    cfg.category = _int(raw, 'category', 0, 0, None, 'Subject area')
    cfg.region_level = _choice(raw, 'regionLevel', tuple(REGION_LEVELS), 'auto', label='Region detail')

    hours = raw.get('trendingHours')
    cfg.trending_hours = _int({'trendingHours': hours}, 'trendingHours', 24, 1, None, 'Trending period')
    if cfg.trending_hours not in TRENDING_HOURS:
        raise InputError(f'Trending period (trendingHours) must be one of 4, 24, 48 or 168 hours, not {hours}.')
    by_id = {str(v): k for k, v in TRENDING_CATEGORIES.items()}
    cfg.trending_category = _choice(raw, 'trendingCategory', ('all', *TRENDING_CATEGORIES), 'all',
                                    aliases=by_id, label='Trend topic')
    cfg.news_per_trend = _int(raw, 'newsPerTrend', 3, 0, MAX_NEWS, 'News stories')
    cfg.max_items = _int(raw, 'maxItems', 0, 0, None, 'Number of trends')
    return cfg
