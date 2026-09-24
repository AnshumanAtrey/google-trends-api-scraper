"""
Turn the per-request rows into the OUTPUT summary. Pure functions, no network.

"ok" on a row means a 2xx answer whose body parsed as that endpoint's expected shape.
"throttled" means HTTP 429 or a redirect to google.com/sorry (Google's block page).
Latency counts only requests that got an HTTP answer; a timeout is not a latency.
"""
import math
from collections import Counter
from statistics import median

GOOGLE_ENDPOINTS = ('warmup', 'explore', 'multiline', 'relatedsearches', 'comparedgeo', 'rss', 'autocomplete')
OTHER_ENDPOINTS = ('ipify', 'ipinfo', 'tls')


def p90(values: list[int]) -> int | None:
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


def _where(r: dict) -> dict:
    return {k: r.get(k) for k in ('googleSeq', 'endpoint', 'keyword', 'keywordIndex', 'attempt', 'sessionNo',
                                  'elapsedSecs', 'status')}


def block(rows: list[dict]) -> dict:
    """Counts, rates and latency for one group of rows."""
    n = len(rows)
    codes = Counter('none' if r['status'] is None else str(r['status']) for r in rows)
    ok = sum(1 for r in rows if r['ok'])
    lat = [r['latencyMs'] for r in rows if r['status'] is not None]
    first429 = next((r for r in rows if r['status'] == 429), None)
    first_thr = next((r for r in rows if r['throttled']), None)
    return {
        'requests': n,
        'ok': ok,
        'successRate': round(ok / n, 3) if n else None,
        'statusCounts': dict(sorted(codes.items())),
        'count429': codes.get('429', 0),
        'countThrottled': sum(1 for r in rows if r['throttled']),
        'countOther': sum(1 for r in rows if not r['ok'] and not r['throttled']),
        'first429': _where(first429) if first429 else None,
        'firstThrottled': _where(first_thr) if first_thr else None,
        'latencyMsMedian': round(median(lat)) if lat else None,
        'latencyMsP90': p90(lat),
        'bytesTotal': sum(r['bytes'] for r in rows),
    }


def keyword_block(results: list[dict]) -> dict:
    """results: one dict per keyword tried, {keyword, index, attempts, throttled, ok, points}."""
    thr = [k for k in results if k['throttled']]
    recovered = [k for k in thr if k['ok']]
    return {
        'tried': len(results),
        'succeeded': sum(1 for k in results if k['ok']),
        'with429': len(thr),
        'recoveredAfterRetry': len(recovered),
        'failedAfterRetries': len(thr) - len(recovered),
        'failedWithout429': sum(1 for k in results if not k['ok'] and not k['throttled']),
        'retriesUsed': sum(k['attempts'] - 1 for k in results),
        'retriesRecovered': (len(recovered) > 0) if thr else None,
        'firstThrottledKeywordIndex': thr[0]['index'] if thr else None,
    }


def points_block(rows: list[dict]) -> dict:
    pts = [r['count'] for r in rows if r['endpoint'] == 'multiline' and r['ok']]
    return {
        'keywordsWithPoints': sum(1 for p in pts if p > 0),
        'min': min(pts) if pts else None,
        'median': median(pts) if pts else None,
        'max': max(pts) if pts else None,
    }


def summarize(rows: list[dict], keyword_results: list[dict]) -> dict:
    google = [r for r in rows if r['google']]
    per_endpoint = {ep: block([r for r in rows if r['endpoint'] == ep])
                    for ep in GOOGLE_ENDPOINTS + OTHER_ENDPOINTS if any(r['endpoint'] == ep for r in rows)}
    return {
        'google': block(google),
        'perEndpoint': per_endpoint,
        'keywords': keyword_block(keyword_results),
        'multilinePoints': points_block(rows),
    }
