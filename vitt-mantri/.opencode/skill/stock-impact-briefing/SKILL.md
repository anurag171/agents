---
name: stock-impact-briefing
description: Use when producing stock-market news briefings, ticker impact analysis, earnings/macro/policy transmission, watchlists, or direct vs indirect equity maps. Trigger for vitta-mantri, stocks, NSE, BSE, Nifty, Sensex, Nasdaq, Fed, RBI, crude, USDINR, FPI, earnings, and "what does this news mean for this stock".
---

# Stock impact briefing

Run this skill whenever Vitta-Mantri researches markets. It is the operating checklist. Do not skip steps to sound fast.

## 1. Clock

Fetch current UTC. State IST, US, London, Tokyo session status. Do not brief weekend rumors as if cash markets are open without saying so.

## 2. Gather in parallel

Minimum fetches:

- One global risk query (geopolitics, rates, credit, oil)
- One India query if the user is India-centric or unspecified (NSE/BSE, RBI, FPI, Union Budget, SEBI)
- User ticker / company / sector if named
- The binding cross-asset print (USDINR, Brent, UST 10Y, copper, VIX — whichever the event implies)

Prefer primary sources: exchange filings, SEC, company IR, central banks. Then wires. Then newspapers. Social is rumor.

## 3. Filter

Keep an event only if at least one is true:

- It changes expected cash flows
- It changes the risk-free rate, spread, or equity risk premium
- It changes scarcity/positioning/index flows
- It is moving the tape **and** you can explain why

Drop recycled explainers, price-target theater, and unsourced tips.

## 4. Grade sources

A primary filing, B multi-wire, C single quality outlet, D rumor, F junk. Only A/B drive the base case. D is a scenario.

## 5. Build the graph

For each kept event:

- Direct issuer + dual listings
- 1 hop: customers, suppliers, competitors, lenders, listed parents/subs
- 2 hop: commodities, FX, rates, policy echo, labor/monsoon
- 3 hop: index/ETF, factors, F&O, crowding, reflexive funding

Name real tickers. Label unverified membership `hypothesis`.

## 6. Force a magnitude

trivial / modest / material / structural / existential

Plus horizon: T+0, T+5, T+60, T+252. Tape and value often disagree — say which you mean.

## 7. Second-order questions (answer at least 4)

Hidden winner if true. Hidden winner if false. Crowding unwind. Policy reversal. Confirming cross-asset. Earnings vs multiple vs flow. Reflexivity (pledge, covenants, funding). Base rate of similar events.

## 8. Write the brief

Emit BUY / SELL / WATCH lists. Lead with the user's stock if they named one. Rank by **unpriced** financial impact, not headline volume.

Always include: meaning of each headline, direct vs indirect hop, confidence 1-5, and that this is an educational map not a personal order.

## 9. India-global translation

When relevant, map:

- US rates -> FPI flows, INR, banks, real estate, quality vs high-beta
- USDINR -> IT/pharma exporters vs oil/electronics importers
- Brent -> OMCs, aviation, paints, upstream
- China data -> metals, chemicals, EMS
- Middle East / shipping -> energy, defense, freight
- US bank/tech spend -> India IT

## 10. Failure modes to avoid

- Briefing from training data as if live
- Sector-ETF laundry lists with no mechanism
- Averaging contradictory sources into fake certainty
- Treating broker target-price changes as news
- Ignoring that the move already happened
- Mixing operating companies with promoter holdcos
- Geopolitical essays with no tickers
- Guaranteeing returns
