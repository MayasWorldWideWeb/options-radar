# Options Radar

Scans Yahoo's most-active stocks + biggest gainers/losers for cheap calls and puts.

- Live page: https://webbymaya.com/radar/index.html (reads the `radar_scan` row in the Web By Maya Supabase project)
- GitHub Action `scan` rescans every ~60s in market hours (each run loops 11 min), every 3 h otherwise.
- Local: `.venv/bin/python -u server.py` → http://localhost:8790 (3-min rescans)
- One-off CLI: `.venv/bin/python scan.py --expiry YYYY-MM-DD NVDA META`

Data: Yahoo Finance via yfinance, ~15 min delayed. Not financial advice.
