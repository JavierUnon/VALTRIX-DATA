
"""VALTRIX - Download US stock prices from Alpaca IEX."""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

INDEX = Path("data/index.json")
OUTPUT = Path("data/prices.json")
REPORT = Path("data/prices_report.json")

API_URL = "https://data.alpaca.markets/v2/stocks/bars"
BATCH_SIZE = 40


def fetch_batch(symbols, start, end, key, secret):
    params = urllib.parse.urlencode({
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start,
        "end": end,
        "feed": "iex",
        "limit": 10000,
        "sort": "desc",
    })

    request = urllib.request.Request(
        API_URL + "?" + params,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
    )

    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                request, timeout=60
            ) as response:
                return json.load(response)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def main():
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")

    if not key or not secret:
        raise SystemExit("Missing Alpaca GitHub secrets.")

    companies = json.loads(
        INDEX.read_text(encoding="utf-8")
    )
    symbols = sorted({
        company["ticker"] for company in companies
    })

    now = datetime.now(timezone.utc)
    start = (
        now - timedelta(days=14)
    ).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    prices = {}
    errors = []

    for offset in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[offset:offset + BATCH_SIZE]

        try:
            result = fetch_batch(
                batch, start, end, key, secret
            )

            for ticker, bars in result.get(
                "bars", {}
            ).items():
                if not bars:
                    continue

                latest = max(
                    bars, key=lambda bar: bar["t"]
                )

                prices[ticker] = {
                    "ticker": ticker,
                    "price": latest["c"],
                    "currency": "USD",
                    "bar_timestamp": latest["t"],
                    "source": "Alpaca",
                    "feed": "iex",
                    "price_type": "IEX daily bar close",
                    "fetched_at_utc": now.isoformat(),
                }

            print(
                f"Processed {min(offset + BATCH_SIZE, len(symbols))}"
                f"/{len(symbols)} symbols"
            )

        except Exception as exc:
            errors.append({
                "symbols": batch,
                "error": str(exc),
            })
            print(f"ERROR: {exc}")

        time.sleep(1)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(prices, indent=2),
        encoding="utf-8",
    )

    missing = [
        ticker for ticker in symbols
        if ticker not in prices
    ]

    REPORT.write_text(
        json.dumps({
            "total": len(symbols),
            "prices_found": len(prices),
            "missing": missing,
            "errors": errors,
            "source": "Alpaca IEX",
            "generated_at_utc": now.isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    print(
        f"Finished: {len(prices)}/{len(symbols)} prices"
    )

    if not prices or errors:
        raise SystemExit(
            "Price import incomplete. Check report and logs."
        )


if __name__ == "__main__":
    main()
