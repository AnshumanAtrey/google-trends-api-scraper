"""
Google Trends answers -> dataset rows. Pure functions, no network.

Shapes verified against Google's real answers on 2026-09-24 (the saved bodies are in
tests/fixtures/):

- Every /trends/api/ body starts with an anti-XSSI guard: )]}' then a newline (explore,
  autocomplete) or )]}', (widgetdata). JSON starts at the first {, whatever the guard.
- explore lists widgets by id. One keyword: TIMESERIES, GEO_MAP, RELATED_TOPICS,
  RELATED_QUERIES. A comparison of 2 to 5: one TIMESERIES and one GEO_MAP
  (fe_multi_heat_map, each region's split between the keywords in percent), then per keyword
  i: GEO_MAP_i and RELATED_QUERIES_i. RELATED_TOPICS comes back empty, so it is never read.
- multiline: timelineData[] with time (unix seconds as a string, UTC bucket start),
  value[i] and hasData[i] per keyword, isPartial on the unfinished last bucket; averages[i]
  only in a comparison.
- comparedgeo: geoMapData[] with geoCode (cities have coordinates instead), geoName,
  value[i], hasData[i]. Rows with hasData false are Google saying "not enough data".
- relatedsearches: rankedList[0] = top, [1] = rising; a rising query's formattedValue is
  "Breakout" or a growth label such as "+2,650%".
- Trending Now (batchexecute rpc i0OFE): a chunked body; each chunk is a JSON array and the
  answer is ["wrb.fr","i0OFE","<JSON string>",...]. The string decodes to [null,[items]] and
  each item is a 13-element tuple, mapped in trend_row().
- The trending RSS is the 10-item fallback: title, ht:approx_traffic "10000+", pubDate and
  ht:news_item entries without dates.
"""
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode

from .inputs import geo_name, keyword_type

# Google's Trending Now topic ids (trends.google.com/trending, 2026-09-24).
TREND_TOPICS = {
    1: 'Autos and Vehicles', 2: 'Beauty and Fashion', 3: 'Business and Finance', 4: 'Entertainment',
    5: 'Food and Drink', 6: 'Games', 7: 'Health', 8: 'Hobbies and Leisure', 9: 'Jobs and Education',
    10: 'Law and Government', 11: 'Other', 13: 'Pets and Animals', 14: 'Politics', 15: 'Science',
    16: 'Shopping', 17: 'Sports', 18: 'Technology', 19: 'Travel and Transportation', 20: 'Climate',
}
STEPS = {'MINUTE': 'minute', 'EIGHT_MINUTE': '8 minutes', 'SIXTEEN_MINUTE': '16 minutes', 'HOUR': 'hour',
         'DAY': 'day', 'WEEK': 'week', 'MONTH': 'month'}
INTRADAY = ('MINUTE', 'EIGHT_MINUTE', 'SIXTEEN_MINUTE', 'HOUR')
RESOLUTIONS = {'COUNTRY': 'country', 'REGION': 'region', 'CITY': 'city', 'DMA': 'metro'}
HT = '{https://trends.google.com/trending/rss}'


def api_json(body: bytes | str) -> dict:
    """Parse a /trends/api/ body. ValueError when it is not the expected JSON object."""
    text = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    start = text.find('{')
    if start < 0:
        raise ValueError('Google sent no JSON')
    data = json.loads(text[start:])
    if not isinstance(data, dict):
        raise ValueError('Google sent JSON that is not an object')
    return data


def iso(unix: int | str | None, with_time: bool = True) -> str | None:
    if unix in (None, ''):
        return None
    dt = datetime.fromtimestamp(int(unix), tz=timezone.utc)
    return dt.isoformat() if with_time else dt.date().isoformat()


# ----------------------------------------------------------------------- explore --
def explore_widgets(body: bytes) -> dict[str, dict]:
    """Widgets that carry a token, by id. ValueError when there is none."""
    out: dict[str, dict] = {}
    for w in api_json(body).get('widgets') or []:
        if isinstance(w, dict) and w.get('id') and w.get('token') and isinstance(w.get('request'), dict):
            out.setdefault(w['id'], w)
    if not out:
        raise ValueError('Google sent no charts for this search')
    return out


def related_widget_id(index: int, count: int) -> str:
    return 'RELATED_QUERIES' if count == 1 else f'RELATED_QUERIES_{index}'


def period(widgets: dict) -> dict:
    """startDate, endDate and step from the widget requests (no extra request needed)."""
    ts = (widgets.get('TIMESERIES') or {}).get('request') or {}
    time = ts.get('time')
    if not time:
        items = ((widgets.get('GEO_MAP') or {}).get('request') or {}).get('comparisonItem') or [{}]
        time = items[0].get('time')
    start = end = None
    if time:
        parts = time.replace('\\', '').split()
        if len(parts) == 2:
            start, end = parts[0][:10], parts[1][:10]
    resolution = ts.get('resolution')
    return {'startDate': start, 'endDate': end,
            'step': STEPS.get(resolution, resolution.lower().replace('_', ' ') if resolution else None),
            'intraday': resolution in INTRADAY}


def with_resolution(widget: dict, resolution: str | None) -> dict:
    """A GEO_MAP widget asking for another region level. Google's token does not cover the
    resolution field (tested 2026-09-24: REGION -> CITY answered 200), so only that changes."""
    if not resolution or widget['request'].get('resolution') == resolution:
        return widget
    return {**widget, 'request': {**widget['request'], 'resolution': resolution}}


# ------------------------------------------------------------------- widgetdata --
def timeline(body: bytes, count: int, intraday: bool) -> tuple[list[list[dict]], list[bool], list[int | None]]:
    """(points per keyword, whether each keyword has any real data point, Google's averages)."""
    data = api_json(body)['default']
    series: list[list[dict]] = [[] for _ in range(count)]
    has = [False] * count
    for point in data.get('timelineData') or []:
        values = point.get('value') or []
        flags = point.get('hasData') or []
        date = iso(point.get('time'), with_time=intraday)
        for i in range(count):
            series[i].append({'date': date, 'value': int(values[i]) if i < len(values) else 0,
                              'isPartial': bool(point.get('isPartial', False))})
            if i < len(flags) and flags[i]:
                has[i] = True
    averages = data.get('averages') or []
    return series, has, [int(averages[i]) if i < len(averages) else None for i in range(count)]


def regions(body: bytes, count: int) -> list[list[dict]]:
    """Regions with data per keyword, busiest first. Cities carry no code, so regionCode is null."""
    out: list[list[dict]] = [[] for _ in range(count)]
    for geo in api_json(body)['default'].get('geoMapData') or []:
        values = geo.get('value') or []
        flags = geo.get('hasData') or []
        for i in range(count):
            if i < len(flags) and flags[i] and i < len(values):
                out[i].append({'regionName': geo.get('geoName'), 'regionCode': geo.get('geoCode'),
                               'value': int(values[i])})
    for rows in out:
        rows.sort(key=lambda r: -r['value'])
    return out


def related(body: bytes) -> dict:
    """{'top': [{query, value}], 'rising': [{query, value, growth}]}."""
    ranked = api_json(body)['default'].get('rankedList') or []

    def items(i: int) -> list[dict]:
        return (ranked[i].get('rankedKeyword') or []) if i < len(ranked) else []

    top = [{'query': k.get('query'), 'value': int(k.get('value') or 0)} for k in items(0) if k.get('query')]
    rising = [{'query': k.get('query'), 'value': int(k.get('value') or 0), 'growth': k.get('formattedValue')}
              for k in items(1) if k.get('query')]
    return {'top': top, 'rising': rising}


def autocomplete(body: bytes) -> list[dict]:
    return [{'title': t.get('title'), 'type': t.get('type'), 'topicId': t.get('mid')}
            for t in api_json(body)['default'].get('topics') or [] if t.get('mid')]


# ------------------------------------------------------------------------ rows --
def trends_url(keywords: list[str], cfg) -> str:
    params = {'q': ','.join(keywords), 'date': cfg.time_range}
    if cfg.geo:
        params['geo'] = cfg.geo
    if cfg.category:
        params['cat'] = cfg.category
    if cfg.google_property:
        params['gprop'] = cfg.google_property
    return 'https://trends.google.com/trends/explore?' + urlencode(params, quote_via=quote, safe=',')


def keyword_row(keyword: str, keywords: list[str], cfg, when: dict, scraped_at: str, *,
                points: list[dict] | None, average: int | None, region_level: str | None,
                region_rows: list[dict] | None, related_queries: dict | None, missing: list[str]) -> dict:
    """One keyword row with every key present. None for a part means it was not asked for."""
    return {
        'keyword': keyword,
        'keywordType': keyword_type(keyword),
        'comparedWith': [k for k in keywords if k != keyword] if cfg.mode == 'compare' else [],
        'geo': cfg.geo,
        'geoName': geo_name(cfg.geo),
        'timeRange': cfg.time_range,
        'startDate': when.get('startDate'),
        'endDate': when.get('endDate'),
        'step': when.get('step'),
        'category': cfg.category,
        'property': cfg.search_property,
        'interestOverTime': points or [],
        'averageInterest': average if cfg.mode == 'compare' else None,
        'regionLevel': region_level,
        'interestByRegion': region_rows or [],
        'relatedQueries': related_queries or {'top': [], 'rising': []},
        'missing': missing,
        'trendsUrl': trends_url(keywords, cfg),
        'scrapedAt': scraped_at,
    }


# ------------------------------------------------------------------ trending --
def batch_items(body: bytes | str, rpc: str = 'i0OFE') -> list[list]:
    """The trend tuples from a batchexecute answer. ValueError when the rpc gave no list."""
    text = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    start = text.find('[')
    if start < 0:
        raise ValueError('Google sent no Trending Now data')
    decoder = json.JSONDecoder()
    pos = start
    while pos < len(text):
        # Chunks are "<length>\n<JSON array>"; the arrays are read with raw_decode, so the lengths
        # (counted in UTF-16 units by Google) never matter.
        while pos < len(text) and (text[pos].isspace() or text[pos].isdigit()):
            pos += 1
        if pos >= len(text):
            break
        chunk, pos = decoder.raw_decode(text, pos)
        for entry in chunk if isinstance(chunk, list) else []:
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == 'wrb.fr' and entry[1] == rpc:
                if not isinstance(entry[2], str):
                    raise ValueError(f'Google answered the Trending Now call with an error: {json.dumps(entry)[:200]}')
                payload = json.loads(entry[2])
                items = payload[1] if isinstance(payload, list) and len(payload) > 1 else None
                return [i for i in items or [] if isinstance(i, list) and i and isinstance(i[0], str)]
    raise ValueError('Google sent no Trending Now list')


def _at(seq, i):
    return seq[i] if isinstance(seq, list) and len(seq) > i else None


def _first(seq):
    return _at(seq, 0)


def trend_row(item: list, geo: str, hours: int, news_limit: int, scraped_at: str) -> dict:
    """One Trending Now tuple -> row. Index map (checked over 1,435 items on 2026-09-24):
    0 term, 1 articles, 2 geo, 3 [start], 4 [end] or null while active, 6 volume bucket,
    8 increase %, 9 grouped searches, 10 topic ids."""
    news = []
    for art in (_at(item, 1) or [])[:news_limit]:
        if not isinstance(art, list) or not _at(art, 1):
            continue
        news.append({'title': _at(art, 0), 'url': _at(art, 1), 'source': _at(art, 2),
                     'publishedAt': iso(_first(_at(art, 3))), 'imageUrl': _at(art, 4)})
    end = _first(_at(item, 4))
    return {
        'term': _at(item, 0),
        'geo': _at(item, 2) or geo,
        'trendingHours': hours,
        'isActive': end is None,
        'startedAt': iso(_first(_at(item, 3))),
        'endedAt': iso(end),
        'searchVolume': _at(item, 6),
        'increasePercent': _at(item, 8),
        'categories': [TREND_TOPICS.get(c, str(c)) for c in _at(item, 10) or []],
        'relatedSearches': [s for s in _at(item, 9) or [] if isinstance(s, str)],
        'news': news,
        'scrapedAt': scraped_at,
    }


def has_topic(item: list, topic_id: int | None) -> bool:
    return topic_id is None or topic_id in (_at(item, 10) or [])


def rss_rows(body: bytes, geo: str, news_limit: int, scraped_at: str) -> list[dict]:
    """The trending RSS feed (Google's 10-item list) as trend rows. It has no growth, end time,
    topics or grouped searches, and its window is Google's, so trendingHours is null."""
    # Google's own feed. ElementTree never resolves external entities, and the expat bundled with
    # Python 3.13 refuses entity-expansion bombs, so defusedxml would add a dependency for nothing.
    root = ET.fromstring(body)  # noqa: S314
    if root.tag != 'rss':
        raise ValueError('the trending feed is not RSS')
    rows = []
    for item in root.findall('./channel/item'):
        traffic = (item.findtext(f'{HT}approx_traffic') or '').replace(',', '').rstrip('+').strip()
        try:
            started = parsedate_to_datetime(item.findtext('pubDate') or '').astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            started = None
        news = [{'title': n.findtext(f'{HT}news_item_title'), 'url': n.findtext(f'{HT}news_item_url'),
                 'source': n.findtext(f'{HT}news_item_source'), 'publishedAt': None,
                 'imageUrl': n.findtext(f'{HT}news_item_picture')}
                for n in item.findall(f'{HT}news_item')][:news_limit]
        rows.append({
            'term': item.findtext('title'),
            'geo': geo,
            'trendingHours': None,
            'isActive': None,
            'startedAt': started,
            'endedAt': None,
            'searchVolume': int(traffic) if traffic.isdigit() else None,
            'increasePercent': None,
            'categories': [],
            'relatedSearches': [],
            'news': news,
            'scrapedAt': scraped_at,
        })
    return rows
