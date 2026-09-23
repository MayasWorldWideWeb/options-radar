# Options Radar

Scans Yahoo's most-active stocks + biggest gainers/losers for cheap calls and puts.

- Live page: https://webbymaya.com/radar.html (reads `scan.json` from the `data` branch)
- GitHub Action `scan` refreshes the data every 5 min in market hours.
- Local: `.venv/bin/python -u server.py` → http://localhost:8790 (3-min rescans)
- One-off CLI: `.venv/bin/python scan.py --expiry YYYY-MM-DD NVDA META`

Data: Yahoo Finance via yfinance, ~15 min delayed. Not financial advice.
