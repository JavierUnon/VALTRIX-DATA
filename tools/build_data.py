"""Build a small, auditable US equity fundamentals sample from SEC Company Facts.

Run with SEC_USER_AGENT='Valtrix Research contact@example.com' python tools/build_data.py
This first version deliberately does not calculate FCFF or market prices.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

COMPANIES = {"AAPL": "0000320193", "MSFT": "0000789019", "AMZN": "0001018724"}
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "debt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
}
OUT = Path("data/companies")

def fetch(cik, user_agent):
    req = urllib.request.Request(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        headers={"User-Agent": user_agent, "Host": "data.sec.gov"},
    )
    # Avoid compressed transfer encoding to keep urllib handling straightforward.
    req.remove_header("Accept-Encoding")
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.load(response)

def latest_annual(facts, candidates):
    gaap = facts.get("us-gaap", {})
    for tag in candidates:
        units = gaap.get(tag, {}).get("units", {})
        for unit in ("USD", "shares"):
            rows = [r for r in units.get(unit, []) if r.get("form") == "10-K" and r.get("fy") and r.get("fp") == "FY" and r.get("end") and r.get("filed")]
            # Avoid duplicate older filing contexts: prefer the latest filing's latest FY context.
            if rows:
                r = max(rows, key=lambda x: (x["end"], x["filed"]))
                return {"value": r["val"], "unit": unit, "period_end": r["end"], "filed": r["filed"], "accession": r.get("accn"), "tag": tag, "form": r["form"]}
    return None

def main():
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        raise SystemExit("Set SEC_USER_AGENT to a descriptive name and a valid contact email before running.")
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for ticker, cik in COMPANIES.items():
        try:
            payload = fetch(cik, user_agent)
            facts = payload.get("facts", {})
            record = {
                "ticker": ticker, "cik": cik, "company_name": payload.get("entityName"),
                "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                "financials": {key: latest_annual(facts, tags) for key, tags in TAGS.items()},
                "review_required": True,
                "note": "Raw SEC fields only. Do not treat operating cash flow minus capex as unlevered FCFF. Debt tags and periods need review.",
            }
            (OUT / f"{ticker}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            index.append({"ticker": ticker, "company_name": record["company_name"], "path": f"data/companies/{ticker}.json"})
            print(f"Saved {ticker}")
        except Exception as exc:
            print(f"ERROR {ticker}: {exc}")
        time.sleep(0.3)
    (Path("data") / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    if len(index) != len(COMPANIES):
        raise SystemExit("Some companies failed; see log.")

if __name__ == "__main__":
    main()
