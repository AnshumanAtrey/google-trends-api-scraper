# Google Trends Scraper API - Interest Over Time, Related Queries

Google Trends scraper and unofficial Google Trends API with no Google account and no API key. Type keywords and get Google's search interest over time, a side-by-side comparison of up to 5 keywords, interest by country, state or city, the top and rising related queries, every Trending Now search with its news articles, and the topics Google matches a word to.

Use it as a pytrends alternative or a SerpApi Google Trends alternative: pull Google Trends in Python, JavaScript, n8n, Make or an AI agent through the Apify API or the Apify MCP server, and export the rows as JSON, CSV or Excel. **$0.02 per keyword**, **$0.08 more for its related queries**, **$0.0009 per Trending Now search**. No start fee, platform usage included. Only the keywords are required.

---

## What does it do?

You type one or more keywords and press Start. Each keyword comes back as one dataset row with the same keys every time, so it drops straight into a spreadsheet, a database or an AI pipeline.

- **Interest over time.** Google's 0 to 100 series for the keyword. Google picks the step from the time period: minutes for the past hour, hours for the past 7 days, days for 3 months, weeks for 12 months. It goes back to 2004, and a custom period takes any two dates. Every point has a UTC date, and the last one says when it is still partial.
- **Compare up to 5 keywords.** The keywords go to Google in one request, like typing them side by side on trends.google.com, so every value shares one scale. Google's own average for each keyword comes with them.
- **Interest by region.** Which countries, states or provinces, or cities search for the keyword most. Regions Google has no data for are left out instead of filled with zeros.
- **Related queries, top and rising.** Up to 25 top and 25 rising searches, with Google's growth labels such as "Breakout" and "+2,650%".
- **Trending Now.** Every Google trending search in a country over the past 4, 24 or 48 hours or 7 days, not only the 10 in Google's RSS feed. Each one has its search volume, growth, start and end time, topics, the searches Google groups under it and news articles about it. If Google's full list does not answer, the actor falls back to the RSS feed and says so in the run summary.
- **Suggestions.** The topics Google matches a word to, each with its id. For `bitcoin`: Bitcoin (Cryptocurrency), Blockchain.com, Cryptocurrency ATM and 2 more.
- **Any Google search, place, time and subject.** Web, News, Images, YouTube or Shopping search. Worldwide or one country. Any of Google's 1,427 categories.

## How is it different from other Google Trends scrapers?

We read the input schemas and READMEs of every Google Trends actor on the Apify Store with 40 or more users in the last 30 days, on 2026-09-24: 8 actors, two of them for Trending Now only. How many of the 8 offer each feature:

| Feature | Other Google Trends actors (of 8) | This actor |
|---|---|---|
| Compare 2 to 5 keywords on one scale | 4 (one of them through commas inside one search term) | Yes, as a plain list |
| Related queries, top and rising | 5 | Yes |
| Every Trending Now search, not only the 10 to 20 in Google's RSS feed | 3 | Yes |
| Every Trending Now search with its news articles | 1 | Yes, 0 to 10 articles per trend |
| Keyword data and Trending Now in one actor | 4 | Yes |
| Web, News, Images, YouTube and Shopping search | 3 | Yes |
| Custom date range | 3 | Yes |
| Topic suggestions (the topic ids Google matches a word to) | 1 | Yes |
| Start fee per run | 5 of 8 charge one | None |
| Platform usage billed to you on top of the price | 2 of 8 | No, included |

One more thing you only notice when something goes wrong:

- **You pay only for keywords that came back.** When Google has too little search data for a keyword, or still throttles it after a retry and two fresh sessions, the keyword is not saved and costs nothing. The run summary names it and says why. When one part of a row is empty, for example related queries for a rare word, the row lists it in `missing` and related queries are not charged, so an empty list never looks like Google's answer.

Related topics are not included. The endpoints this actor uses sent an empty related-topics list in all 3 tries on 2026-09-24, while related queries came back full. apify's Google Trends Scraper did return related topics that day (run `Wsjyg51mE7OXfXwOR`).

## When should I use it?

- SEO and content: find rising queries around a topic before they peak, and pick between two phrasings by comparing them.
- Market and product research: compare brands or products on one scale over 12 months or 5 years, and see which states or countries search most.
- Newsrooms and social teams: pull every Trending Now search for a country every hour, with the news articles behind each one.
- Finance and research models: a weekly or daily interest series as a signal for crypto, stocks, elections or product launches.
- AI agents: answer "is interest in X rising?" with Google's real series, through the Apify API or the Apify MCP server.
- Academic research: a dated record of search interest back to 2004.

## How much does it cost to scrape Google Trends?

Pay per event: **$0.02 per keyword** for interest over time and interest by region, **$0.08 more per keyword for related queries** (so **$0.10** for a keyword with all three parts), **$0.0009 per Trending Now search** ($0.90 per 1,000) and **$0.002 per keyword looked up for suggestions**. No start fee, no monthly fee, platform usage included.

| Run | Charged | Price |
|---|---|---|
| The example input (2 keywords, past 12 months, worldwide, all three parts) | 2 keywords + 2 related queries | $0.20 |
| One keyword, interest over time and regions | 1 keyword | $0.02 |
| One keyword with related queries too | 1 keyword + 1 related queries | $0.10 |
| A comparison of 5 keywords (interest over time and regions) | 5 keywords | $0.10 |
| 100 keywords with all three parts | 100 keywords + 100 related queries | $10.00 |
| 1,000 keywords, interest over time only | 1,000 keywords | $20.00 |
| Trending Now, United States, past 24 hours (220 trends on 2026-09-24) | 220 trends | $0.198 |
| Trending Now, India, past 7 days (1,182 trends on 2026-09-24) | 1,182 trends | $1.06 |
| Suggestions for 10 keywords | 10 lookups | $0.02 |
| A run where Google returned no data | 0 | $0.00 |

A keyword costs the same whichever of interest over time and regions you ask for, and in a comparison each keyword counts once. Related queries are charged only when Google returns at least one. Apify's free plan includes $5 of monthly credit, which covers about 250 keywords with interest over time and regions, 50 keywords with related queries as well, or 5,500 Trending Now searches. Set a spending limit on a run for a hard cap: the actor does not start a keyword the remaining limit cannot pay for, stops cleanly when the limit is reached, and every row you paid for is saved.

## How does the price compare with other Google Trends scrapers?

The same jobs with every Google Trends actor on the Apify Store that had 40 or more users in the last 30 days on 2026-09-24, ordered by those users. Prices come from each actor's live pricing that day on the Apify free plan at its default memory (some start fees are charged per GB of memory). For apify's actor, whose platform usage is billed to you, we ran both jobs on our own account and quote the bill (note 1).

- **Job 1:** one keyword, United States, past 12 months, with interest over time, interest by state and related queries.
- **Job 2:** 10 such keywords, in as few runs as each actor allows.

| Scraper | Job 1 | Job 2 | Start fee | Platform usage | Compare | Regions | Related queries | Trending Now | Failed or timed out, 30 days |
|---|---|---|---|---|---|---|---|---|---|
| **This actor** | **$0.10** | **$1.00** | **None** | **Included** | **Yes, 2 to 5** | **Yes** | **Yes** | **Yes, every trend** | **New** |
| [Google Trends Scraper](https://apify.com/apify/google-trends-scraper) by apify | $0.111 measured (1) | $0.72 for 3 of the 10, then stopped at 900 s (1) | None | Billed to you (default 4 GB) | Yes, commas inside one search term | Yes | Yes | No | 25.9% |
| [Google Trends Scraper](https://apify.com/data_xplorer/google-trends-fast-scraper) by data_xplorer | $0.022, without related queries | $0.22, 10 runs, without related queries | $0.02 | Included | No | Yes, inside one country | No | Yes, up to 1,000 | 0.2% |
| [Google Trends Scraper](https://apify.com/agenscrape/google-trends-scraper) by agenscrape | $0.125 | $0.35 | $0.10 (4 GB) | Included | No (2) | Yes | Yes | No | 0.1% |
| [Google Trends Scraper - Interest, Regions & Queries](https://apify.com/khadinakbar/google-trends-scraper) by khadinakbar | about $0.77 + platform usage (3) | about $7.70 + platform usage, 2 runs | $0.00005 | Billed to you | Yes, 1 to 5 | Yes | Yes | Yes | 10.1% |
| [Google Trends Daily Scraper - Real-Time Trending Keywords API](https://apify.com/vnx0/google-trends-scraper) by vnx0 | - | - | None | Included | - | - | - | Yes, RSS list | 0.1% |
| [Google Trends Scraper - Interest, Regions & Trends](https://apify.com/scrapesage/google-trends-scraper) by scrapesage | $0.004 | $0.04 | None | Included | Yes, 2 to 5 | Yes | Yes | Yes, every trend | 0.0% |
| [Google Trends Scraper](https://apify.com/automation-lab/google-trends-scraper) by automation-lab | $0.143 | $1.39, 2 runs | $0.005 | Included | Yes, 2 to 5 | Yes | Yes | Yes, RSS list | 0.8% |
| [Google Trends Trending Now](https://apify.com/data_xplorer/google-trends-trending-now) by data_xplorer | - | - | $0.02 | Included | - | - | - | Yes, every trend | 0.0% |

(1) Measured on our Apify account on 2026-09-24 with apify's default input and its default 4 GB of memory. One keyword (bitcoin, US) ran 300.7 seconds and billed $0.1077 of platform usage plus $0.003 for the result (run `Wsjyg51mE7OXfXwOR`). Ten keywords in one run hit the 900-second limit our test set, after saving 3 of them, and billed $0.709 of platform usage plus $0.009, or $0.24 per saved keyword (run `abuI6GZ4QJjbmQ8qb`; apify's own default time limit is 7 days). Its rows also carry related topics and city-level interest, which this actor does not return. Platform usage depends on your plan.
(2) Its README suggests comparing up to 5 terms in one run. An open issue on its Issues tab reports that each keyword is fetched on its own scale.
(3) It charges $0.005 per data point: 53 weekly values, 51 US regions and up to 50 related queries make about 154 points, counted from Google's own answers on 2026-09-24. It takes 1 to 5 keywords per run, and its default cap of 500 records per run has to be raised for job 2.

For one keyword with related queries, this actor costs less than apify, agenscrape, automation-lab and khadinakbar. For 10 keywords in one go, scrapesage ($0.04), data_xplorer without related queries ($0.22) and agenscrape ($0.35) cost less than this actor.

"Failed or timed out" is the share of each actor's runs in the last 30 days that ended FAILED or TIMED-OUT, from Apify's public run statistics. Timed-out runs include runs stopped by a time limit their caller set.

Trending Now on its own, every US trend of the past 24 hours (220 on 2026-09-24):

| Scraper | Per trend | Start fee | 220 US trends | News articles |
|---|---|---|---|---|
| **This actor** | **$0.0009** | **None** | **$0.198** | **Yes, 0 to 10 per trend** |
| data_xplorer's Google Trends Scraper | $0.002 per row, and the whole list is one row (per its README example) | $0.02 | $0.022 | No |
| scrapesage | $0.0015 | None | $0.33 | Yes |
| data_xplorer's Google Trends Trending Now | $0.001 | $0.02 | $0.24 | No |
| automation-lab | $0.00115 | $0.005 | RSS list only: 10 to 20 trends for $0.017 to $0.028 (its README says about 20; Google's US feed had 10 on 2026-09-24) | Yes |
| vnx0 | $0.0012 | None | RSS list only, up to 10 a day for $0.012 | Yes |
| khadinakbar | $0.005 + platform usage | $0.00005 | Its README does not say how many trends a run returns | Yes |

Several of these actors discount their prices on paid Apify plans. On paid tiers apify's fee per result falls to between $0.001 and $0.0001, but its platform usage (about $0.108 per keyword in our one-keyword run) stays on your bill. This actor has one price on every plan. For a bare list of trend names without news, data_xplorer's Google Trends Scraper costs less than this actor.

## Which inputs does it take?

Every field has a plain name with its JSON key in brackets, so you can fill the form or send the same key through the API.

| Field (JSON key) | Required | What it does |
|---|---|---|
| Report type (`mode`) | No | `keywords` (the default): each keyword on its own 0 to 100 scale. `compare`: 2 to 5 keywords on one shared scale. `trending`: Trending Now for a country. `suggestions`: the topics Google matches each keyword to |
| Keywords (`searchTerms`) | Yes, except for `trending` | Search terms, one per line, or topic ids from `suggestions` such as `/m/05p0rrx` (the Bitcoin topic) |
| Country (`geo`) | No | Empty for worldwide, or a country code such as `US`, `GB` or `IN`. For `trending`, the country to read (default `US`) |
| Time period (`timeRange`) | No | `now 1-H` (past hour), `now 4-H`, `now 1-d`, `now 7-d`, `today 1-m`, `today 3-m`, `today 12-m` (the default), `today 5-y`, `all` (since 2004), or two dates such as `2024-01-01 2024-06-30` (from 2004-01-01 up to today) |
| Data to fetch (`dataTypes`) | No | The parts per keyword: `interestOverTime`, `interestByRegion`, `relatedQueries`. All three by default. Related queries cost $0.08 more per keyword; fewer parts also finish sooner |
| Search type (`property`) | No | `web` (the default), `news`, `images`, `youtube` or `froogle` (Google Shopping) |
| Subject area (`category`) | No | A Google Trends category number. `0` is all subjects (the default). `7` Finance, `5` Computers & Electronics, `71` Food & Drink, `20` Sports |
| Region detail (`regionLevel`) | No | `auto` (the default) lets Google choose: countries for worldwide, states or provinces inside a country. Or `country`, `region` (state or province), `city` |
| Trending period (`trendingHours`) | No | The Trending Now window: `"4"`, `"24"` (the default), `"48"` or `"168"` hours |
| Trend topic (`trendingCategory`) | No | `all` (the default), or one of Google's 19 Trending Now topics such as `sports`, `technology` or `politics` |
| News stories (`newsPerTrend`) | No | News articles to attach to each trend, 0 to 10. Default 3 |
| Number of trends (`maxItems`) | No | The most Trending Now rows to save, to cap the cost. `0` (the default) saves every trend |

Coming from pytrends or SerpApi? The same settings under their names:

| This actor | pytrends | SerpApi |
|---|---|---|
| `searchTerms` | `kw_list` | `q` |
| `timeRange` | `timeframe` | `date` |
| `geo` | `geo` | `geo` |
| `category` | `cat` | `cat` |
| `property` | `gprop` | `gprop` |
| `dataTypes` | one call per part: `interest_over_time()`, `interest_by_region()`, `related_queries()` | `data_type` |
| `regionLevel` | `resolution` | |
| `trendingHours` | | `hours` (Trending Now API) |
| `trendingCategory` | | `category_id` (Trending Now API, as a number) |

Through the API:

```json
{
  "mode": "compare",
  "searchTerms": ["bitcoin", "ethereum", "solana"],
  "geo": "US",
  "timeRange": "today 12-m"
}
```

## What does the output look like?

One row per keyword, per Trending Now search or per suggestions lookup. Each column has a plain name with its JSON key in brackets, the same labels you see in Console. The Output tab has five views: Keywords, Interest over time, Interest by region, Trending Now and Topic suggestions.

Keyword rows (`keywords` and `compare` modes):

| Column (JSON key) | What it holds |
|---|---|
| Search term (`keyword`) | The keyword or topic id you typed |
| Term or topic (`keywordType`) | `Search term`, or `Topic` for a topic id |
| Compared with (`comparedWith`) | The other keywords in a comparison, empty otherwise |
| Country code (`geo`), Place (`geoName`) | Empty and `Worldwide`, or the country |
| Time period (`timeRange`), From (`startDate`), To (`endDate`) | The period asked for and its first and last day, UTC |
| Point spacing (`step`) | `minute`, `hour`, `day`, `week` or `month` |
| Subject area (`category`), Search type (`property`) | The filters used |
| Timeline (`interestOverTime`) | Points with Period start (`date`), Interest 0-100 (`value`) and Still counting? (`isPartial`) |
| Average 0-100 (`averageInterest`) | Google's average in a comparison, `null` otherwise |
| Region detail (`regionLevel`) | `country`, `region` or `city` |
| Regions (`interestByRegion`) | Region code (`regionCode`, `null` for cities, which Google sends without a code), Region (`regionName`) and Interest 0-100 (`value`) |
| Also searched (`relatedQueries`) | Most searched (`top`) and Fastest rising (`rising`): Related search (`query`), Score (`value`), Rise % or Breakout (`growth`) |
| Empty parts (`missing`) | Parts you asked for that Google sent empty |
| Google Trends link (`trendsUrl`) | The same view on trends.google.com |
| Fetched at (`scrapedAt`) | When the answer came from Google, UTC |

Trending Now rows: Trending search (`term`), Country code (`geo`), Trending period (`trendingHours`), Still trending? (`isActive`), Started (`startedAt`), Ended (`endedAt`), Approx. searches (`searchVolume`), Growth % (`increasePercent`), Trend topics (`categories`), Also trending (`relatedSearches`) and News stories (`news`), each with Headline (`title`), Link (`url`), Publisher (`source`), Published (`publishedAt`) and Image (`imageUrl`). Rows from the RSS fallback have no growth, end time, topics or article dates, and `trendingHours` is `null` because Google sets the feed's window.

Suggestions rows: Search term (`keyword`) and Matching topics (`suggestions`), each with Topic id (`topicId`), Topic name (`title`) and Topic kind (`type`).

The row below is built from Google's real answers for `bitcoin`, worldwide, past 12 months, on 2026-09-24. The real row has 53 weekly points, 64 countries and 25 top and 25 rising queries; 3 of each are shown here:

```json
{
  "keyword": "bitcoin",
  "keywordType": "Search term",
  "comparedWith": [],
  "geo": "",
  "geoName": "Worldwide",
  "timeRange": "today 12-m",
  "startDate": "2025-09-24",
  "endDate": "2026-09-24",
  "step": "week",
  "category": 0,
  "property": "web",
  "interestOverTime": [
    { "date": "2025-09-21", "value": 40, "isPartial": false },
    { "date": "2026-02-01", "value": 100, "isPartial": false },
    { "date": "2026-09-20", "value": 34, "isPartial": true }
  ],
  "averageInterest": null,
  "regionLevel": "country",
  "interestByRegion": [
    { "regionCode": "CH", "regionName": "Switzerland", "value": 100 },
    { "regionCode": "AT", "regionName": "Austria", "value": 87 },
    { "regionCode": "DE", "regionName": "Germany", "value": 79 }
  ],
  "relatedQueries": {
    "top": [
      { "query": "bitcoin price", "value": 100 },
      { "query": "bitcoin usd", "value": 34 },
      { "query": "bitcoin news", "value": 24 }
    ],
    "rising": [
      { "query": "how to buy bitcoin safely", "value": 18750, "growth": "Breakout" },
      { "query": "clarity act", "value": 2650, "growth": "+2,650%" },
      { "query": "bitcoin clarity act", "value": 2550, "growth": "+2,550%" }
    ]
  },
  "missing": [],
  "trendsUrl": "https://trends.google.com/trends/explore?q=bitcoin&date=today%2012-m",
  "scrapedAt": "2026-09-24T06:32:58+00:00"
}
```

In `compare` mode each keyword still gets its own row. `comparedWith` names the others, the values share one scale, `averageInterest` is Google's average for this keyword, and each region value is this keyword's share of the compared searches there, in percent. Part of the `ethereum` row from a comparison of 5 coins, US, past 7 days, News search (Google's real answer on 2026-09-24):

```json
{
  "keyword": "ethereum",
  "comparedWith": ["bitcoin", "solana", "dogecoin", "xrp"],
  "geo": "US",
  "timeRange": "now 7-d",
  "step": "hour",
  "property": "news",
  "averageInterest": 8,
  "interestOverTime": [
    { "date": "2026-09-17T06:00:00+00:00", "value": 4, "isPartial": false },
    { "date": "2026-09-24T06:00:00+00:00", "value": 13, "isPartial": true }
  ],
  "interestByRegion": [
    { "regionCode": "US-CA", "regionName": "California", "value": 17 },
    { "regionCode": "US-NY", "regionName": "New York", "value": 17 },
    { "regionCode": "US-TX", "regionName": "Texas", "value": 15 }
  ]
}
```

A Trending Now row, from Google's US list for the past 24 hours on 2026-09-24 (image links shortened):

```json
{
  "term": "meta vr glasses",
  "geo": "US",
  "trendingHours": 24,
  "isActive": true,
  "startedAt": "2026-09-23T23:50:00+00:00",
  "endedAt": null,
  "searchVolume": 50000,
  "increasePercent": 1000,
  "categories": ["Technology"],
  "relatedSearches": ["meta vr glasses", "meta glasses", "meta", "vr glasses", "meta connect", "meta connect 2026"],
  "news": [
    {
      "title": "Meta Launches Lightweight $1,299 VR Headset That Looks Like Glasses",
      "url": "https://www.bloomberg.com/news/articles/2026-09-23/meta-launches-1-299-vr-headset-that-look-like-glasses-to-rival-apple-vision-pro",
      "source": "Bloomberg.com",
      "publishedAt": "2026-09-23T23:41:36+00:00",
      "imageUrl": "https://encrypted-tbn2.gstatic.com/images?q=tbn:ANd9GcQ-LIElz2n..."
    },
    {
      "title": "Meta is betting big on its Muse AI agent with a blitz of new gadgets",
      "url": "https://www.cnn.com/2026/09/24/tech/meta-muse-ai-glasses-connect",
      "source": "CNN",
      "publishedAt": "2026-09-24T04:01:29+00:00",
      "imageUrl": "https://encrypted-tbn1.gstatic.com/images?q=tbn:ANd9GcTE6QfFP4TO..."
    },
    {
      "title": "Meta unveils $1,299 VR Glasses, camera-less smart glasses at Meta Connect",
      "url": "https://finance.yahoo.com/technology/article/meta-unveils-1299-vr-glasses-camera-less-smart-glasses-at-meta-connect-235611909.html",
      "source": "Yahoo Finance",
      "publishedAt": "2026-09-23T23:56:11+00:00",
      "imageUrl": "https://encrypted-tbn3.gstatic.com/images?q=tbn:ANd9GcTn2yjPYgR0..."
    }
  ],
  "scrapedAt": "2026-09-24T06:36:28+00:00"
}
```

`searchVolume` is Google's rough bucket, the lower bound of what trends.google.com shows as "50K+".

A suggestions row (Google's real answer for `bitcoin`, 3 of its 5 topics shown):

```json
{
  "keyword": "bitcoin",
  "suggestions": [
    { "topicId": "/m/05p0rrx", "title": "Bitcoin", "type": "Cryptocurrency" },
    { "topicId": "/m/0_1fcmj", "title": "Blockchain.com", "type": "Topic" },
    { "topicId": "/m/0_lgq95", "title": "Cryptocurrency ATM", "type": "Topic" }
  ],
  "scrapedAt": "2026-09-24T06:34:26+00:00"
}
```

The run summary is the `OUTPUT` record (Console: the Output tab; API: the default key-value store, key `OUTPUT`). It lists every keyword you typed with its status (`ok`, `partial` or `skipped`), a reason code (`ok`, `no_data`, `throttled`, `invalid`, `error`, `limit` or `time`), the reason in plain English, and which parts were missing, plus the charged events and the number of requests sent to Google. It is refreshed about every 10 seconds while the run goes. There is no summary row in the dataset, so CSV and Excel exports hold data rows only.

## How fast is it?

Measured on Apify at 256 MB on 2026-09-24, with a research build that sends the same requests to Google. Unless a line says otherwise, it fetched interest over time only (past 12 months, worldwide), with 1.5 seconds between requests to Google:

- 15 keywords: **54 to 72 seconds** of fetching in 6 runs (61 to 77 seconds of Apify run time, start-up included), from Apify's own servers and through Apify's datacenter and US residential proxies.
- Throttling: 8 runs with 1.5 to 3 seconds between requests sent 275 requests to Google. Google throttled 2 of them (HTTP 429; one was in a test that skipped the cookie on purpose), and one retry recovered each, so all 115 keywords came back.
- With only 0.3 seconds between requests from one address, fetching interest over time, regions and related queries, Google throttled 18 of 117 requests and 3 of 30 keywords failed (retries were off in that test). This is why the actor keeps its requests spaced.
- Memory peaked under 75 MB in all 9 runs, so 256 MB is plenty.

Interest by region and related queries add 2 requests per keyword, so a full keyword is 4 requests. With more than 8 keywords the actor runs up to 6 sessions in parallel: its own connection plus up to 5 sessions on Apify's datacenter proxy, each with its own IP, one more for every 8 keywords. Each keyword stays in one session from start to finish, because Google ties its answers to the address that asked. If Google or the proxy refuses a proxy session on every try, that session stops taking keywords and hands its keyword back, so the run gets slower but loses nothing.

The release build, run locally from a home connection on 2026-09-24 (one session):

- The example input (2 keywords, all three parts): 18 seconds, 9 requests to Google.
- A comparison of 3 keywords, US, past 7 days, all three parts: 15 seconds, 7 requests.
- Trending Now, US, past 24 hours: 216 trends in 8 seconds, 2 requests.
- Suggestions for 2 keywords: 5 seconds, 3 requests.

Runs on Apify with many keywords and parallel sessions will be measured and added here.

## Common questions

### Is this an alternative to Google Trends Scraper by apify, data_xplorer, agenscrape or scrapesage?

Yes, for interest over time, interest by region, related queries, keyword comparison and Trending Now: $0.10 per keyword with related queries or $0.02 without, platform usage included and no start fee (see the price tables above). It does not return related topics or US metro (DMA) regions.

### Is there an official Google Trends API?

Only as a closed test. Google announced a Trends API alpha on 2025-07-24 for "a very limited number of testers", and on 2026-09-24 its page still said it was accepting applications, with no prices or quotas published. The announcement describes consistently scaled interest over time for the last 5 years, up to 2 days ago. Trending Now and related queries are not part of it.

### Does pytrends still work?

Not reliably. The pytrends library is archived on GitHub, its last release (4.9.2) came out on 2023-04-13, and the old daily and real-time trending endpoints it used answer 404 (checked 2026-09-24). This actor returns the same kinds of data with nothing to install or maintain, and the inputs section above maps each pytrends parameter to its key here.

### Are the numbers search counts?

No. Google scales interest from 0 to 100 within each request: 100 is the busiest point for that place and time, and 50 is half as busy. So two keywords fetched separately cannot be compared; use `compare` mode for that. Trending Now is the exception: `searchVolume` is Google's rough count bucket, such as 50000 for "50K+".

### How do I compare keywords fairly?

Use `"mode": "compare"` with 2 to 5 keywords. They go to Google in one request, as when you type them side by side on trends.google.com, so every value shares one scale. Each keyword still gets its own row, with `comparedWith` naming the others.

### What do "Breakout" and "+2,650%" mean in rising queries?

They are Google's growth labels for queries that rose the most compared with the period before. "+2,650%" is the growth Google reports, and "Breakout" is the label Google shows instead of a percentage for the fastest risers. `value` always carries Google's raw number (18750 for "how to buy bitcoin safely" in the example).

### Why are related topics missing?

Because the endpoints this actor uses sent an empty list of related topics in all 3 tries on 2026-09-24, while related queries came back full. Rather than return empty lists, this actor leaves them out. apify's Google Trends Scraper returned related topics the same day (18 top and 9 rising for bitcoin), so use it if you need them.

### Why do other tools get "429 Too Many Requests", and does this actor handle it?

Yes, this actor handles it. 429 is Google's throttle page. Google sends it when a request has no Google cookie or one address asks too fast. The actor gets the cookie first and spaces the requests of each session 1.5 seconds apart. After a 429 it waits 5 seconds and retries once in the same session; if Google throttles again, the keyword starts over in a fresh session on a new IP, up to two times, keeping the parts it already has: first on Apify's datacenter proxy, and the last try on Apify's residential proxy. A new cookie jar alone does not help, because Google blocks the address, not the cookie. In the 2026-09-24 test on Apify one retry recovered both 429s in 8 runs (one of them from a test that skipped the cookie on purpose), and all 115 keywords came back. A keyword that still fails costs nothing.

### Why was a keyword skipped?

Because Google had too little search data for it in that place and time, Google still throttled it after the retries, it was longer than 100 characters, or the run reached your spending limit or its time limit before its turn. The run summary gives the reason for each keyword. A skipped keyword is not charged.

### Do I need a Google account, an API key or a proxy?

No. The actor reads the same public data trends.google.com shows. There is no account, key or proxy to set up.

### How far back does the data go?

To 2004. `"timeRange": "all"` asks Google for everything since 2004-01-01. For any other span, give two dates, such as `2024-01-01 2024-06-30`.

### What time zone are the dates in?

UTC. Google's buckets start on UTC boundaries, and the actor reports every date in UTC.

### Why is the last point marked `isPartial`?

The current week, day or hour is not over yet, so Google marks its value as partial. It can change when the period ends.

### Can I track a topic instead of a search term?

Yes. Run `"mode": "suggestions"` on a word to get the topics Google matches it to, each with an id such as `/m/05p0rrx` (Bitcoin, Cryptocurrency). Put that id in `searchTerms` to get the topic's interest, which groups the different ways people search for the same thing.

### How do I check trends every day or every hour?

Save the input as an Apify task and schedule it. For Trending Now, `"trendingHours": "4"` on an hourly schedule catches new trends as they start. Each run returns the full answer again, so keep the newest run.

### How does it compare with SerpApi or DataForSEO?

It needs no subscription and costs $0.10 per keyword with all three parts, or $0.02 without related queries. SerpApi's plans start at $25 a month for 1,000 searches, and each Trends data type is its own search. DataForSEO charges $0.0027 per task in its standard queue (results within 45 minutes) and $0.011 per live task, and a task can hold up to 5 keywords. Both prices are from their pricing pages on 2026-09-24.

### Can I call it from code or an AI agent?

Yes. Start it through the Apify API or the Apify MCP server with the JSON input above and read the dataset. `searchTerms` is a plain list and comparing is its own mode, so there is no comma trick for an agent to get wrong. Every key is always present, empty (`null` or `[]`) when Google has no such part.

## Limitations

- Values are Google's relative 0 to 100 scale within each request, not search counts.
- No related topics: the endpoints this actor uses sent them empty in every test on 2026-09-24.
- Up to 5 keywords per comparison, which is Google's own limit.
- Regions by country, state or province, and city. No US metro (DMA) level and no sub-country `geo` such as `US-CA` yet.
- Keywords and regions with little search volume come back without data. Those keywords are skipped and not charged.
- Requests to Google are spaced 1.5 seconds apart in each session to avoid throttling, so a large run takes time, even with up to 6 sessions in parallel. The run stops starting keywords 60 seconds before its time limit and still writes the summary.
- The actor reads the same web endpoints trends.google.com uses, and Google can change them without notice.

---

## About the maintainer (priority response within 1-2 hours)

Built and maintained by **Anshuman Atrey** ([@AnshumanAtrey](https://github.com/AnshumanAtrey)).

- Purple-team security researcher, 5x hackathon winner
- Co-founder of **Walrus Securitas** (AI cybersecurity SaaS) and **The Drone Syndicate** (autonomous defence drones)
- Author of the OSINT and data actor portfolio on Apify Store: 16 shipped actors covering email, phone, username, IP and domain, network, secret, social, LinkedIn, domain history, Telegram and Indian fintech data

### Custom feature requests shipped within 1-2 hours (priority)

If you need a field, a filter or an output format this actor does not have, the maintainer ships it directly into this actor, typically within 1-2 hours for priority requests during active hours and within 24 hours overnight. This is direct one-to-one service from the maintainer, not a contractor queue.

**Fastest contact channels (ranked by response speed):**
1. **LinkedIn DM** -> [linkedin.com/in/anshumanatrey](https://linkedin.com/in/anshumanatrey), typically under 1 hour during active hours
2. **GitHub issue** on this actor's repo
3. **Apify Console** DM to `@anshumanatrey`
4. **Email** via [atrey.dev](https://atrey.dev)

---

## Sibling actors by the same maintainer

| Actor | Use case |
|---|---|
| [telegram-channel-scraper](https://apify.com/anshumanatrey/telegram-channel-scraper) | Public Telegram channel -> posts, media links, inline buttons, reactions (no login) |
| [domain-history-contact-osint](https://apify.com/anshumanatrey/domain-history-contact-osint) | Dead or expired domain -> previous owner's emails, phones, people + orgs, WHOIS + Wayback history, source URL on every row |
| [social-analyzer](https://apify.com/anshumanatrey/social-analyzer) | Username -> profiles across 900+ social sites with confidence scoring |
| [instagram-profile-intel-no-login](https://apify.com/anshumanatrey/instagram-profile-intel-no-login) | Instagram username -> bio emails + phones + 25 fields (no login) |
| [theharvester-osint](https://apify.com/anshumanatrey/theharvester-osint) | Domain -> emails + subdomains + IPs from 54+ public sources |
| [linkedin-harvester](https://apify.com/anshumanatrey/linkedin-harvester) | Email -> best-match public LinkedIn profile URL + confidence score |
| [netintel](https://apify.com/anshumanatrey/netintel) | IP or domain -> unified WHOIS + DNS + GeoIP + ASN + ports |
| [holehe-email-osint](https://apify.com/anshumanatrey/holehe-email-osint) | Email -> registered accounts across 120+ platforms |
| [phoneinfoga-phone-osint](https://apify.com/anshumanatrey/phoneinfoga-phone-osint) | International phone -> country, footprint URLs, OSINT trail |
| [bug-bounty-finder](https://apify.com/anshumanatrey/bug-bounty-finder) | Domain -> active HackerOne + Bugcrowd + security.txt programs |
| [nmap-scanner](https://apify.com/anshumanatrey/nmap-scanner) | Network -> port + service + version detection, NSE scripts |
| [gitleaks-github-secret-scanner](https://apify.com/anshumanatrey/gitleaks-github-secret-scanner) | GitHub -> leaked API keys across 30+ services |
| [betterleaks-cloud](https://apify.com/anshumanatrey/betterleaks-cloud) | GitHub + S3 -> leaked secrets with live vendor-API validation |
| [upi-id-osint](https://apify.com/anshumanatrey/upi-id-osint) | Indian phone or VPA -> active UPI IDs + bank-registered name from NPCI |
| [yt-dlp-video-link-extractor](https://apify.com/anshumanatrey/yt-dlp-video-link-extractor) | Any video URL -> direct stream and download links + metadata, 1000+ sites |
| [zomato-restaurant-scraper](https://apify.com/anshumanatrey/zomato-restaurant-scraper) | Zomato city page -> restaurants with phone, address, cuisines, rating and cost for two |

---

## Documentation

- Apify Store: https://apify.com/anshumanatrey/google-trends-api-scraper
- GitHub repo: https://github.com/AnshumanAtrey/google-trends-api-scraper
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Issues / feature requests: open an issue on the GitHub repo or DM LinkedIn for the fastest response
- License: MIT

## Last updated

2026-09-24 (version 1.0)
