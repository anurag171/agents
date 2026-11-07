# Vitta-Mantri: context, approach and a critical assessment

Written at branch `feat/dynamic-universe-ipo-assets-ui` (15 commits including this one; about 54 files and roughly +10,200/-750 lines over `main` before the documentation was added).
Almost all of that code was written by an AI assistant in one long session and verified by tests the same assistant wrote.
Read section 9 before trusting any number this system prints.

---------------------------------------------------------------------------------------------------

## 1. Short verdict

1. **It is a well-instrumented hypothesis, not a proven edge.** Nothing in it has been shown to make money. The track record is empty. Every weight, threshold and rule is a judgment call (a few were calibrated against simulated data, never against real outcomes).
2. **Its most reliable value today is organisation**: one place that collects headlines from ~14 sources, de-duplicates them, maps them to affected stocks and assets, shows live prices, charts and IPOs, and screens the whole market by fundamentals and price strength. That is useful for triage and research.
3. **Its most honest feature is the accountability loop**: every call is logged, settled against what the price did next, and shown with a range that accounts for same-day correlation. If the system has no edge, this loop will say so, but only after months.
4. **The riskiest parts are not the algorithms**: live network parsers (NSE, Yahoo) were written from memory and never run against real responses; there is no real-browser test, no container build, no human review of the 10k-line diff; and the admin token fails open.
5. **Do not use the output to trade real money, or distribute it to others, until the checklist in `TODO.md` section 3 (P0) is done and the pre-registered criteria in section 10 are met.** Distributing BUY/SELL recommendations may be regulated (see 9.1).

---------------------------------------------------------------------------------------------------

## 2. What it is and is not

**Is:** a single-user / small-team research dashboard for Indian and global equities (plus commodities, FX, rates, indices, crypto, IPOs). A Flask app with one background scheduler, a SQLite database, and a static JavaScript UI.

**Is not:** a trading system (no orders, no positions, no sizing, no risk limits), a backtester (it only measures forward from the day it is switched on), an investment adviser, or a data vendor.

Three kinds of output, with different time horizons that the UI shows side by side but that should not be mixed:

| Output | Basis | Horizon it is judged on |
|---|---|---|
| **Signals** (BUY / SELL / WATCH with a strength %) | headlines mapped to affected tickers, gated by chart, market reaction and strength | 1-5 trading days (judged at 1/5/20/60) |
| **Discovery** (PICK / WATCHLIST) | fundamentals + 6-12 month momentum + stability, no news needed | months (judged at 20/60 days) |
| **IPO insight** (FAVOURABLE / NEUTRAL / CAUTION) | public terms and, if supplied, fundamentals | listing day vs long term |

---------------------------------------------------------------------------------------------------

## 3. Architecture at a glance

```
 news sources (14: RSS first, HTML fallback only where robots.txt allows)
        |  parallel, timeouts, retries, per-source circuit breaker, 45 s deadline
        v
  cluster same story across outlets -> ONE event (+ corroboration, wire copy counts once)
        |
        v
  decode: theme keywords (word-boundary) -> sector/asset rules -> ticker impacts   [optional LLM interpretation]
        |
        v
  board rows: net score (age-decayed) -> market reaction (priced in?) -> chart check -> strength % -> gates
        |                                                     |
        |         live quotes (NSE snapshot, Yahoo)  <---------+      price history (Yahoo chart)
        v
  shared snapshot file  <--- written by the ONE elected scheduler process (file lock)
        |                      also: assets board, IPO board, signals log (SQLite)
        v
  gunicorn web workers (stateless readers) -> JSON API + SSE -> static UI (8 tabs)

  background jobs (same scheduler process, separate threads / rate limits):
    settle outcomes (1/5/20/60 days) -> track record, theme health (demotion), tuning data
    screen: universe -> prices + fundamentals -> factor ranks -> PICKs, weekly panel snapshot
```

**Process model.** `gunicorn -w N` starts N identical processes. Each runs a tiny supervisor; exactly one wins an OS file lock (`scheduler.lock`) and becomes the scheduler. If it dies, another takes over within ~15 s. Web workers never scrape; they serve the snapshot the scheduler wrote atomically. `VM_ROLE=web` makes a process never compete for the lock.

---------------------------------------------------------------------------------------------------

## 4. Modules (what each file is for)

| File | Lines | Role |
|---|---:|---|
| `app.py` | 418 | HTTP routes, scheduler election, cycle orchestration (`_build`), background jobs |
| `engine/scraper.py` | 569 | 14 news sources; fetch layer (`_get` timeouts/backoff, conditional GET, robots.txt); parallel `scrape_all`; `python -m engine.scraper` audit |
| `engine/cluster.py` | 74 | same-story clustering (token Jaccard + shared numbers, 48 h window, wire-copy detection) |
| `engine/decoder.py` | 294 | theme matching, impacts, sentiment nudge, board aggregation, recency decay, corroboration |
| `engine/universe.py` | 513 | **legacy** hardcoded seed: 130 companies, 14 themes (still the fallback and the source of tag overrides) |
| `engine/instruments.py` | 283 | dynamic universe: NSE index/listing CSVs, SEC tickers, drop-in files, grows from live quotes; `python -m engine.instruments` diagnostic |
| `engine/impacts.py` | 81 | theme -> instrument impacts resolved at runtime (curated pins + sector rules at 0.75 weight) |
| `engine/ai.py` | 160 | optional LLM interpretation of headlines (**not hardened; see 9.5**) |
| `engine/movers.py` | 204 | live quote snapshot, gainers/losers/active, priced-in downgrade, unexplained-mover rows |
| `engine/bars.py` | 66 | Yahoo daily price history (cached) |
| `engine/technicals.py` | 390 | indicators, support/resistance, entry zone, analog history, CONFIRM/NEUTRAL/WAIT/CONTRADICT |
| `engine/chartgate.py` | 48 | applies the chart verdict to board rows (downgrade-only) |
| `engine/scoring.py` | 154 | strength % (6 weighted components, coverage shrink), minimum-strength gate |
| `engine/assets_feed.py` | 80 | 16 assets (gold, crude, USDINR, US10Y, Nifty, BTC ...) with news pressure and chart verdict |
| `engine/ipo.py`, `ipo_feed.py` | 117 + 175 | IPO parsing (NSE lists, files, headlines) and a transparent rule-based score |
| `engine/store.py` | 99 | SQLite (WAL), versioned migrations v1-v4, 10 tables |
| `engine/signal_log.py` | 465 | call logging, outcome settling, cluster-aware statistics, theme health/demotion |
| `engine/tuning.py` | 204 | held-out tuning of news-score weights (CLI) |
| `engine/fundamentals.py` | 167 | fundamentals from files / SQLite cache / Yahoo (progressive fill) |
| `engine/screener.py` | 199 | news-independent factor screen |
| `engine/screen_panel.py`, `screen_tuning.py` | 77 + 159 | weekly whole-market snapshot; held-out tuning of screen weights (CLI) |
| `engine/snapshot.py`, `locks.py`, `resilience.py`, `log.py` | small | atomic shared snapshot, flock, circuit breaker, structured logging |
| `static/app.js`, `style.css`, `templates/index.html` | 565 / 102 / 115 | the UI (DOM built with `textContent`; no inline scripts; no external fonts) |
| `tests/` | 156 tests | pytest (offline, synthetic) + `tests/ui/run.js` (jsdom) |

---------------------------------------------------------------------------------------------------

## 5. Approach and the reasoning behind it

### 5.1 Evidence-first
The original app mapped keywords to BUY/SELL with no price data and no feedback. The central design choice since then: **never present a number without a way to find out whether it was right.** Hence the signals log (5.8), the track record, theme health and the tuning protocols.

### 5.2 The strength percentage
"82%" means *how much independent, agreeing evidence stands behind the call*. It is **not** the probability of profit and is labelled `calibrated=false`.
Six components, fixed weights: news strength 30%, chart check 25%, independent confirmation 15%, evidence agreement 10%, source quality 10%, market reaction 10%. Missing components are not guessed: the score is pulled toward 50% in proportion to what is missing, and with <70% data coverage the label never exceeds "Strong".
*Why:* a single number is what users want; the breakdown, coverage and caps are there so it cannot silently overstate.
*Weakness:* the weights are my judgment and the integer precision implies accuracy that does not exist (9.3).

### 5.3 Gates that can only downgrade
BUY/SELL becomes WATCH if: strength < 60% (`VM_MIN_SIGNAL_PCT`), the chart contradicts or says "wait", price has already moved 3%+ in the signal's direction (priced in), or the signal's primary theme has been demoted. The original call is kept (`news_action`) and **still logged as a shadow call**, so the gates can be A/B-tested. Gates never create or upgrade a signal.

### 5.4 One story, one event
The same story from five outlets used to count five times. Articles are clustered (similarity of tokens plus shared numbers, 48 h window); the best-graded source represents the cluster; weight rises with independent outlets, capped at 1.75x; PTI/IANS/ANI copy counts as one origin.

### 5.5 Recency
News weight halves every 12 hours (`VM_NEWS_HALFLIFE_H`); headlines without a real timestamp are capped at 0.5.

### 5.6 Chart confirmation
Trend, momentum, extension, support/resistance (confirmed pivots only), an entry zone from a 1.5 risk:reward rule, and how this stock behaved after similar chart states in its own past (non-overlapping samples; ignored under 20 analogs). A BUY against a confirmed downtrend is vetoed unless history supports it. All indicators use only past bars.

### 5.7 Dynamic universe and the news-independent screen
Coverage should not depend on what the news mentions. The universe loads from NSE lists and grows from every symbol in the live quote snapshot. The screen ranks each stock within its sector peers on quality, growth, value, 6-12 month relative momentum and stability, with hard filters (liquidity, history, no loss-makers, PICK needs an uptrend and >=60% data coverage and a chart check). Without fundamentals a stock can only reach the watchlist.
*Why these factors:* long published track records. *Why not more:* each extra factor is another untested degree of freedom.

### 5.8 Logging, settling and honest statistics
- **Logged:** one row when the news-implied direction changes (not every 90 s), plus shadow calls; screen PICKs as `kind='screen'`.
- **Settled:** entry = price shown at the call; exit = close h trading days after the emission date (1/5/20/60); no look-ahead; excess return vs Nifty 50 / S&P 500 where applicable; **costs not included**.
- **Reported:** per strength bucket, a hit rate with a 95% range from a **bootstrap over whole days**. Calls made on the same day are not independent; a simple formula contained the true rate only ~76-78% of the time in simulation, the day bootstrap ~95%. A percentage is shown only after 30 calls on >=20 separate days. "Does strength predict outcomes?" is tested **within each day** so a bullish day cannot pass for skill.
- **Versioned:** rows carry `scoring_version`; changing the formula starts a fresh record (the current version is `s2`: recency decay).

### 5.9 Governance of the model itself
- **Theme demotion:** a theme is switched to WATCH only if the *upper* 99% bound of its average excess return (over independent theme-days, last 180 days) is <= 0 with >=30 observations. "Unproven" themes keep running. Demoted themes keep being logged and can recover (14-day dwell, margin +0.25%).
- **Weight tuning (news and screen):** time-ordered split, search on train only, choice on validation only, **one look** at test, bootstrap over days/dates, used test windows recorded and blocked from reuse. Nothing is automatic: `adopt` is a deliberate command that bumps the version. Simulated error rates (not real): news tuner adopts on pure noise ~1/40, screen tuner ~1/30, both adopt strong real edges 30/30 and weak ones less than half the time. Needs 300 calls/60 days (news) and ~3.5 years of weekly snapshots (screen) before it will even run.

### 5.10 Storage and operations
SQLite in WAL mode on a persistent volume: one writer, a few readers, no extra service, nothing leaves the network. SQL is plain so Postgres is a connection swap later. JSON log line per cycle, `/health/live` and `/health/ready` probes, non-root Dockerfile (never built here).

---------------------------------------------------------------------------------------------------

## 6. Data model (SQLite, `$VM_DB` or `$VM_DATA/vm.sqlite`)

`signals_log` (append-only calls) | `signal_outcomes` (per call, horizon) | `signal_state` (current direction per ticker, to log changes only) | `theme_gate`, `theme_gate_log` (health + audit trail) | `tuning_runs` (every tuning run, test-look count, adopted flag) | `fundamentals` (7-day cache) | `screen_panel`, `screen_panel_outcomes` (weekly whole-market snapshots) | `meta` (job timestamps).
Other files in `$VM_DATA`: `snapshot.json`, `discovery.json`, `universe.json`, `scheduler.lock`, `build.lock`. Config in `config/`: `themes.json`, `tag_overrides.json`, `assets.json` (symbols marked `verified:false`), optional `scoring_weights.json`, `screen_weights.json` (written only by `adopt`).

## 7. Interfaces

**UI tabs:** Signals, Discovery, Market, Assets, IPOs, Track record, Tape, Sources.
**API:** `GET /api/briefing`, `/stream` (SSE), `/api/discovery`, `/api/performance?horizon=&kind=`, `/api/theme-health`, `/api/chart/<ticker>`, `/api/signals.csv` (admin token), `POST /api/refresh` and `/api/evaluate` (admin token), `/health`, `/health/live`, `/health/ready`.
**CLI:** `python app.py` (dev), `python -m engine.instruments`, `python -m engine.scraper`, `python -m engine.tuning report|adopt|rollback`, `python -m engine.screen_tuning report|adopt|rollback`.

**Environment variables** (defaults in brackets)
- *Core:* `VM_DATA` [data], `VM_DB`, `VM_ROLE` [all|scheduler|web], `VM_HOST` [127.0.0.1], `PORT` [8000], `VM_ADMIN_TOKEN` [unset = **open**], `VM_CONTACT`, `VM_USER_AGENT`, `VM_VERSION`.
- *Scraping:* `VM_SCRAPE_WORKERS` [8], `VM_SCRAPE_DEADLINE_S` [45], `VM_RESPECT_ROBOTS` [1].
- *Data feeds:* `VM_MARKET_SOURCES` [nse,yahoo], `VM_MARKET_TTL_S` [120], `VM_QUOTES_DIR`, `VM_UNIVERSE_DIR`, `VM_UNIVERSE_TTL_H` [24], `VM_UNIVERSE_RETRY_MIN` [30], `VM_FUNDAMENTALS_DIR`, `VM_FUND_BATCH` [120], `VM_FUND_CACHE_DAYS` [7], `VM_IPO_DIR`, `VM_IPO_NSE` [1].
- *Scoring/gates:* `VM_MIN_SIGNAL_PCT` [60], `VM_NEWS_HALFLIFE_H` [12], `VM_REQUIRE_CHART_CONFIRM`, `VM_SCORING_WEIGHTS`, `VM_SCREEN_WEIGHTS`.
- *Jobs/statistics:* `VM_EVAL_EVERY_MIN` [180], `VM_SCREEN_EVERY_H` [12], `VM_SCREEN_FILL_EVERY_MIN` [30], `VM_SCREEN_MAX` [600], `VM_MIN_DAYS` [20], `VM_THEME_MIN_N` [30], `VM_TUNE_BOOT_Q` [0.025].
- *Logging:* `VM_LOG_JSON`, `VM_LOG_LEVEL` [INFO]. *LLM (optional):* `USER_LLM_API_KEY`, `USER_LLM_BASE_URL`, `USER_LLM_MODEL`.

---------------------------------------------------------------------------------------------------

## 8. What has and has not been verified

| Claim | How it was checked | Strength of the check |
|---|---|---|
| Logic of every module | 156 pytest tests, offline, synthetic data written by the same author | Medium: proves internal consistency, not correctness against reality |
| Statistical procedures | simulations with known truth (interval coverage; tuner false-adoption and power) | Medium: assumes Gaussian-ish noise and independent stocks; real data are fatter-tailed and correlated |
| UI behaviour | ~59 checks in jsdom (`tests/ui/run.js`), including a script-injection payload | Low-medium: jsdom is not a browser; layout, mobile and accessibility never seen |
| Multi-process scheduler, shared snapshot, failover | real 4-worker gunicorn runs, scheduler killed, takeover in ~3 s | Good |
| Runs read-only, no HOME, writes only to `VM_DATA` | gunicorn from a read-only copy, probes and JSON log checked | Good proxy; **the Docker image was never built** |
| NSE / Yahoo / IPO endpoints and formats | **not verified at all** (sandbox had no access); parsers written from memory, tested on fixtures I wrote | **None** |
| LLM interpretation | **never run** against a real model | None |
| Real news flow end to end | **never run** (sandbox offline) | None |
| Fundamentals/Yahoo crumb flow | not verified | None |

---------------------------------------------------------------------------------------------------

## 9. Critical assessment (ranked, deliberately harsh)

### 9.1 Fundamental risks (could make the whole thing pointless or unsafe)

1. **No demonstrated edge, and a structural reason to doubt news-driven edge.** Public headlines polled every 90 s through RSS are late (RSS delay plus polling) and widely read; institutions and algorithms price a headline in seconds. A retail system reacting minutes later mostly sees the move already made. The system partly knows this (the priced-in gate), but that only *avoids* late entries; it does not create early ones. Any remaining edge would have to come from slower second-order reasoning (indirect effects), which here is encoded as 14 hand-written themes and ~130 curated pins. Expect modest at best, plausibly nil after costs.
2. **Costs, slippage and taxes are excluded everywhere.** For 1-5 day holding in Indian equities, brokerage, STT, exchange charges, stamp duty, impact cost (large for mid/small caps) and short-term capital gains tax can exceed a small edge. A hit rate of 55% with 0.4% average gains does not survive 0.4-0.7% round-trip costs. The track record currently reports gross numbers.
3. **Regulatory exposure.** Publishing BUY/SELL recommendations with percentages to other people may fall under SEBI's research-analyst and investment-adviser regulations (registration, disclosures, record keeping). A "not advice" footer does not change that. Use it for yourself/internal research unless a compliance review says otherwise. (Not legal advice.)
4. **The percentage will be misread.** Despite labels, "BUY 82%" reads as "82% chance it goes up". The only real defence is the observed hit rate next to it, which will not exist for months.
5. **Factor screens are crowded and have long bad stretches.** Quality/momentum/value tilts are well known; momentum crashes; Indian small caps are volatile and illiquid. The screen finds candidates; it has no market-timing or risk control.

### 9.2 Data and vendor fragility

6. **Every network-facing parser is unverified.** NSE `equity-stockIndices` (including `meta.industry`), NSE IPO endpoints, Yahoo chart/quoteSummary/crumb, Yahoo screener formats: written from memory. The likeliest first-run outcome is that some of them return 0 items. They fail soft (reported as `error` in coverage), but the UI will look empty until fixed.
7. **Unofficial endpoints and terms of service.** Yahoo's endpoints are undocumented and change; NSE blocks datacentre IPs and requires cookies; the site terms of NSE, Yahoo and several news sites restrict automated access. RSS is the sanctioned route; HTML fallback respects robots.txt but is still scraping; NSE is fetched with a browser-style user agent (a deliberate exception). A paid, licensed feed is the robust answer if this is to be relied upon.
8. **No market-calendar or timezone logic.** Calls emitted after the close or on holidays record a stale "price at signal"; the outcome clock is "trading days" inferred from bars. This biases results in an unknown direction. IST vs UTC handling is by date strings only.
9. **Benchmark mismatch.** All Indian stocks are compared with the Nifty 50, including small caps; "excess return" is therefore biased by size. Assets have no benchmark.
10. **Survivorship in the track record.** Calls on stocks that are later suspended or delisted never settle (no price data) and just stay "pending". They are not counted as losses. The record is systematically flattering for exactly the cases that hurt.
11. **Price adjustment.** Yahoo closes are split-adjusted but not dividend-adjusted; a 30% heuristic guards entry prices against splits/bad quotes; corporate actions beyond that are not handled.
12. **Fundamentals quality.** Yahoo's Indian fundamentals can be stale, standalone vs consolidated inconsistent, or missing; D/E, ROE definitions differ by vendor. Banks are special-cased (leverage ignored) but other sectors (NBFCs, real estate) are not.

### 9.3 Statistical honesty

13. **Constants everywhere, none validated:** strength weights, 60%/70% thresholds, 12 h half-life, 0.75 sector-rule weight, shrink-toward-50 formula, RSI bands, 1.5 R:R, 2.5 ATR "extended", 3% priced-in, 30 calls / 20 days / 30 theme-days minimums. They were chosen to be sensible, not tested.
14. **Simulation-calibrated rules assume friendly noise.** The tuners' error rates and the interval coverage figures come from simulated Gaussian data with independent stocks. Real returns are fat-tailed, cross-correlated and regime-dependent; true error rates are probably worse.
15. **Multiple comparisons.** ~14 themes x 2 kinds, several horizons, several buckets, several A/B tables: false "findings" are expected. Only theme demotion (99%) and the tuners have any correction; the track-record tables do not.
16. **"Right" ignores magnitude and risk.** Hit rate counts a +0.1% as equal to +5%. There is no Sharpe-like measure, no drawdown, no equal-weight portfolio curve.
17. **Theme attribution is a heuristic** (primary theme = largest signed weight), and sector-rule additions are not logged separately from curated pins, so we cannot yet tell whether the dynamic additions help.
18. **One regime.** Whatever the first months show will reflect one market phase.
19. **The tuners may be practically unreachable.** Years of data are required before they run, so the system will operate on untuned weights for a long time.

### 9.4 Engineering

20. **A 10,000+ line unreviewed diff.** 15 commits, ~54 files, behaviour changes mixed with new features (strength gate demoting calls, theme demotion, POST-only refresh, user agent, universe growth). It needs splitting into reviewable pull requests and a human read, especially `signal_log.py` (465 lines of SQL and statistics) and `app.py`.
21. **`app.py` is a growing orchestrator** with module-level mutable state (`STATE`, `SCHED`, `_PERF`, caches) and an in-process scheduler thread elected by file lock. It works (tested under gunicorn) but is non-standard; a dedicated scheduler deployment would be simpler to reason about.
22. **Hand-rolled migrations, no backup automation.** The SQLite file is the only copy of the evidence. Losing the volume loses the track record.
23. **Crude decoding is still crude.** Only the top theme per headline is used; sentiment words match as substrings ("loss" inside other words); no magnitude (a 0.1% and a 10% move read the same); static maps. A few hundred hand-labelled headlines would likely expose many errors.
24. **The LLM layer was not touched** beyond its ticker whitelist: only the first 24 headlines, no caching (re-billed each cycle), no JSON-schema validation, untrusted headline text goes into the prompt, AI and rule signals are merged without reconciliation. Prompt injection and cost are open risks.
25. **Security defaults are weak.** `VM_ADMIN_TOKEN` unset means refresh, evaluate and the CSV export are open to anyone who can reach the port. Read endpoints have no authentication. No CSP/security headers (the UI uses `textContent` so injected markup is inert, but there is no defence in depth). No rate limiting. A GitHub token was pasted into a chat during development and must be revoked.
26. **Tests prove consistency, not truth.** 156 offline tests plus jsdom, written by the same author as the code, with no recorded real payloads (contract tests), no CI, unmeasured coverage, no load test. Some tests are slow (tuning).
27. **A regression slipped through and was only found while writing this document:** `python app.py` had stopped serving because an earlier edit removed the `__main__` block. It is fixed and covered by a test; the lesson is that "all tests pass" was not enough.
28. **Docker/OpenShift unverified.** The Dockerfile was never built; OpenShift behaviours (arbitrary UID, RWO volumes with two pods) were reasoned, not tested.

### 9.5 Product and UX

29. **Feature sprawl.** Eight tabs, six-factor bars, A/B tables. For one user this is probably too much; the risk is attention spread across displays nobody can validate. Integer percentages and many metrics create false precision.
30. **No personalisation or risk layer.** No holdings, no position sizing, no portfolio exposure, no stop discipline (stop/target are mechanical chart levels). Signals are context-free.
31. **Mixed horizons without a frame.** A 5-day news call, a 6-month screen pick and an IPO listing view sit side by side.
32. **UI never seen by a human on a real device.** Mobile layout, contrast and keyboard use are untested beyond jsdom.

### 9.6 Process

33. **AI-authored, AI-verified.** Design choices were not reviewed by someone who trades or by a statistician. The theme maps in particular encode opinions about Indian market structure that should be reviewed by a domain expert.
34. **Calibration choices were made while looking at simulations**, then re-measured on fresh simulations; that is fair for the rules themselves but it is still tuning on synthetic data.

### 9.7 What would change this assessment
- Real, net-of-cost results: >=150 settled signal calls over >=60 days whose mean excess return has a day-cluster lower bound above zero after costs (section 10).
- Recorded real payloads for every network parser, running in CI.
- A human review of the themes and of the diff.
- Evidence that high-strength calls beat low-strength calls *within* days.

---------------------------------------------------------------------------------------------------

## 10. How to use it responsibly (pre-registered criteria)

Decide the success rule **before** looking at results, so it cannot be bent afterwards. Suggested (edit to your own costs and tolerance; these numbers are my assumptions):

1. Run it for at least 3 months in paper mode: look, but do not trade.
2. **News signals pass** only if, at the 5-day horizon, with >=150 settled calls on >=60 separate days: mean excess return **net of an assumed 0.5% round-trip cost** has a day-cluster 95% lower bound > 0, **and** the within-day strength test is significant, **and** shown (BUY/SELL) calls beat held-back calls.
3. **Screen picks pass** only if, at 60 days, with >=30 picks on >=20 separate dates: net excess return lower bound > 0 versus an appropriate (not Nifty 50) benchmark.
4. If a criterion fails after the minimum sample, stop using that output; do not loosen the rule.
5. Never size a position from the strength %.

## 11. Glossary
**Call:** a logged BUY/SELL (or screen PICK) at a moment. **Shadow call:** a call the gates downgraded to WATCH, still logged. **Strength:** weighted evidence score, not a probability. **Priced in:** price already moved >=3% in the call's direction. **Independent observation:** one theme-day (or one date), because same-day calls move together. **Excess return:** return minus the benchmark over the same window. **Coverage:** share of the strength weights for which data exists.
