# Google Trends Probe

Private research actor. It measures how Google Trends answers from the place a run's traffic exits (an Apify server, a datacenter proxy or a residential proxy), so the Google Trends scraper can be built on numbers instead of guesses.

## What it does

For each keyword it replays the browser flow of `trends.google.com/trends/explore`, one request at a time with a fixed gap between Google requests:

1. warmup, once per session: `warmup: home` is `GET https://trends.google.com/trends/` (sets the `NID` cookie, about 690 KB), `rss` is the trending RSS (also sets `NID`, about 21 KB), `none` skips it
2. `GET /trends/api/explore?hl=en-US&tz=0&req={comparisonItem:[{keyword,geo,time}],category:0,property:""}`
3. `GET /trends/api/widgetdata/multiline?req=<TIMESERIES widget request>&token=<token>`
4. optional: `/trends/api/widgetdata/relatedsearches` and `/trends/api/widgetdata/comparedgeo`

After the keywords (so `warmup: none` really sends explore without a cookie) it reads the trending RSS (`/trending/rss?geo=US`) and autocomplete (`/trends/api/autocomplete/<first keyword>`) once. Each session also looks up its exit IP (api.ipify.org), and its network owner (ipinfo.io); the first session also records the client's TLS fingerprint (tls.peet.ws). A new session (fresh cookies, new proxy session so a new exit IP) starts every `newSessionEvery` keywords and, when `rotateOn429` is on, before each 429 retry.

## Output

- **Dataset**: one row per request: `endpoint`, `keyword`, `attempt`, `status`, `ok`, `throttled`, `count` (multiline points, RSS items, related queries...), `latencyMs`, `bytes`, `bodySha1` (12 hex chars, spots identical bodies), `httpVersion`, `location`, `retryAfter`, `error`, `snippet` (body start on failure), `nid`, `sessionNo`, `proxySession`, `exitIp`, `elapsedSecs`.
- **OUTPUT record**: `google` totals and `perEndpoint` blocks (requests, ok, success rate, status counts, 429 count, first 429 with its Google request number and time, median and p90 latency), `keywords` (succeeded, with 429, recovered after retry), `multilinePoints`, `sessions` (exit IP, network owner, warmup status, whether NID was set), `rss`, `autocomplete`, `tlsFingerprint`, `elapsedSecs` and the input as used. A proxy group the account cannot use ends the run with `status: proxy_refused` and the error.

## Input

Everything has a default; an empty input runs curl_cffi as Chrome, no proxy, 30 keywords, the home-page warmup, 1.5 s between Google requests, a new session every 10 keywords and up to 2 retries after a 429 (10 s, then 20 s). See the input tab for each field.

## Local run

```bash
uv venv -p 3.13 .venv && uv pip install -p .venv/bin/python -r requirements.txt
mkdir -p storage/key_value_stores/default
echo '{"keywords": ["bitcoin", "weather"], "client": "httpx"}' > storage/key_value_stores/default/INPUT.json
CRAWLEE_STORAGE_DIR=storage .venv/bin/python -m src.main
.venv/bin/python -m unittest discover -s tests
```
