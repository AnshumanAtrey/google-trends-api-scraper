"""
Google Trends endpoints: URLs and body parsers. Pure functions, no network.

The flow a browser runs on trends.google.com/trends/explore, which the probe replays:
  1. GET /trends/                        sets the NID cookie on .google.com
  2. GET /trends/api/explore?req=...     returns one widget per chart, each with a token
  3. GET /trends/api/widgetdata/multiline?req=<TIMESERIES request>&token=...   interest over time
     (optional) /widgetdata/relatedsearches (RELATED_QUERIES), /widgetdata/comparedgeo (GEO_MAP)
Plus two token-free endpoints: the trending RSS and autocomplete.

Every /trends/api/ body starts with an anti-XSSI prefix ()]}' then a comma or newline),
so JSON starts at the first { and the prefix length is never assumed.
"""
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

BASE = 'https://trends.google.com'
WARMUP_URL = f'{BASE}/trends/'
HL = 'en-US'
TZ = '0'                      # minutes from UTC; 0 keeps timestamps in UTC

# Real, varied searches: big and small volume, brands, events, multi-word, one non-ASCII.
DEFAULT_KEYWORDS = [
    'bitcoin', 'taylor swift', 'chatgpt', 'weather', 'iphone 17', 'world cup', 'python', 'tesla',
    'recipe', 'nba', 'ozempic', 'netflix', 'amazon', 'cricket', 'election', 'minecraft', 'flights',
    'yoga', 'gold price', 'mortgage rates', 'air fryer', 'anime', 'stock market', 'ramen',
    'electric car', 'hurricane', 'black friday', 'coffee', 'diwali', 'fußball',
]


def _q(params: dict) -> str:
    return urlencode(params, quote_via=quote)


def _json(obj) -> str:
    """Compact and raw UTF-8, as the browser's JSON.stringify sends it."""
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False)


def explore_url(keyword: str, geo: str, timeframe: str) -> str:
    req = {'comparisonItem': [{'keyword': keyword, 'geo': geo, 'time': timeframe}], 'category': 0, 'property': ''}
    return f'{BASE}/trends/api/explore?' + _q({'hl': HL, 'tz': TZ, 'req': _json(req)})


def widget_url(kind: str, widget: dict) -> str:
    """kind: multiline, relatedsearches or comparedgeo. The widget's own request is sent back verbatim."""
    return f'{BASE}/trends/api/widgetdata/{kind}?' + _q({'hl': HL, 'tz': TZ, 'req': _json(widget['request']),
                                                         'token': widget['token']})


def rss_url(geo: str) -> str:
    return f'{BASE}/trending/rss?' + _q({'geo': geo or 'US'})


def autocomplete_url(keyword: str) -> str:
    return f'{BASE}/trends/api/autocomplete/{quote(keyword, safe="")}?' + _q({'hl': HL, 'tz': TZ})


def api_json(body: bytes) -> dict:
    """Parse a /trends/api/ body. Raises ValueError when it is not the expected JSON object."""
    text = body.decode('utf-8', errors='replace')
    start = text.find('{')
    if start < 0:
        raise ValueError('no JSON object in body')
    data = json.loads(text[start:])
    if not isinstance(data, dict):
        raise ValueError('JSON is not an object')
    return data


def explore_widgets(body: bytes) -> dict[str, dict]:
    """Widgets by id (TIMESERIES, GEO_MAP, RELATED_TOPICS, RELATED_QUERIES); first of each id wins."""
    out: dict[str, dict] = {}
    for w in api_json(body).get('widgets') or []:
        if isinstance(w, dict) and w.get('id') and w.get('token') and 'request' in w:
            out.setdefault(w['id'], w)
    if 'TIMESERIES' not in out:
        raise ValueError('explore returned no TIMESERIES widget')
    return out


def multiline_points(body: bytes) -> int:
    return len(api_json(body)['default']['timelineData'])


def related_count(body: bytes) -> int:
    ranked = api_json(body)['default']['rankedList']
    return sum(len(r.get('rankedKeyword') or []) for r in ranked)


def comparedgeo_count(body: bytes) -> int:
    return len(api_json(body)['default']['geoMapData'])


def autocomplete_count(body: bytes) -> int:
    return len(api_json(body)['default']['topics'])


def rss_items(body: bytes) -> int:
    root = ET.fromstring(body)
    items = root.findall('./channel/item')
    if root.tag != 'rss':
        raise ValueError(f'root element is {root.tag}, not rss')
    return len(items)
