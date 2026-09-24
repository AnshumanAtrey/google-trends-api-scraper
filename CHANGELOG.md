# Changelog

## [1.0] - 2026-09-24

First release of the scraper (replaces the private 0.1 probe that measured Google Trends from Apify).

### Added
- Four reports (`mode`): `keywords` (each keyword on its own 0 to 100 scale), `compare` (2 to 5
  keywords on one shared scale, one row per keyword with `comparedWith` and Google's average),
  `trending` (every Trending Now search in a country, with search volume, growth, start and end
  time, topics, grouped searches and 0 to 10 news articles) and `suggestions` (the topics and
  topic ids Google matches a keyword to).
- Keyword rows with interest over time, interest by region (country, state or province, or city)
  and top and rising related queries with Google's growth labels, whichever `dataTypes` asks for.
  Any Google search type (web, news, images, YouTube, Shopping), country, subject area and time
  period, including two custom dates. Every key is always present; parts Google sent empty are
  listed in `missing`.
- Google's session rules: a cookie warmup per session, every request of a keyword in one session
  (one IP), 1.5 s between requests, a 5 s wait and one retry after a 429, then up to two fresh
  sessions. With more than 8 keywords, up to 5 extra sessions run in parallel on Apify's
  datacenter proxy. A proxy session that Google or the proxy refuses on every try stops taking
  keywords and hands its keyword back, and near the spending limit a session steps aside while
  other sessions hold the rest of the budget instead of ending the run.
- Trending Now falls back to Google's RSS feed (about 10 trends) when the full list fails.
- Pay per event (prices set after the cost test): `keyword` only when a row holds a timeline or
  regions, `related-queries` only when Google sent at least one related query, one `trend` per
  Trending Now row, one `suggestions` per keyword with at least one topic. Rows with no data are
  not saved or charged. A spending limit is checked before each keyword is fetched.
- The `OUTPUT` summary: every keyword with its status, reason code and plain reason, charged
  events and Google request counts, refreshed about every 10 s. A run that saves nothing fails
  with a plain message.

### Measured locally (home IP, one session, 2026-09-24)
- Example input (2 keywords, all parts): 18 s, 9 requests to Google. Compare of 3 keywords, US,
  past 7 days: 15 s, 7 requests. Trending Now US 24 h: 216 trends in 8 s, 2 requests.
  Suggestions for 2 keywords: 5 s, 3 requests.
