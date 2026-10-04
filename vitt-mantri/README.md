# Vitta-Mantri

Live stock-news decoder. Scrapes Yahoo Finance and Economic Times, reads each headline, maps direct and indirect financial impact, and streams a BUY / SELL / WATCH board.

```bash
pip3 install --break-system-packages -r requirements.txt
python3 app.py
```

Device: http://127.0.0.1:8000
JSON: http://127.0.0.1:8000/api/briefing
Force scrape: http://127.0.0.1:8000/api/refresh
SSE stream: http://127.0.0.1:8000/stream

India: Economic Times, Moneycontrol, CNBC-TV18, Business Standard, NSE, MSN Money.
Global: Yahoo Finance, MarketWatch, Investing.com, Seeking Alpha, Bloomberg, Reuters, FT, WSJ.
Bloomberg/Reuters/FT/WSJ often paywall; RSS headlines still map. Light/dark theme in the header.

Educational impact map from public headlines. Not personalized investment advice.
