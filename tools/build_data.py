
"""VALTRIX - S&P 500 financial data importer.
Sources: S&P 500 constituents dataset and SEC EDGAR.
Raw reported figures only; no automatic DCF or FCFF calculation.
"""

import csv
import io
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LIST_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
OUT = Path("data/companies")

TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities"
    ],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment"
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue"
    ],
    "debt_current": [
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
    ],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding"
    ],
}

FLOW_FIELDS = {
    "revenue",
    "operating_cash_flow",
    "capital_expenditure",
}


def download(url, agent):
    headers = {
        "User-Agent": agent,
        "Accept": "application/json, text/csv",
    }
    request = urllib.request.Request(url, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request, timeout=60
            ) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def get_companies(agent):
    content = download(LIST_URL, agent)
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig"))
    )
    companies = []

    for row in reader:
        ticker = row["Symbol"].strip()
        cik = row["CIK"].strip()

        if not ticker or not cik:
            continue

        companies.append({
            "ticker": ticker,
            "cik": str(int(cik)).zfill(10),
            "name": row["Security"],
            "sector": row.get("GICS Sector", ""),
        })

    if len(companies) < 450:
        raise RuntimeError(
            "S&P 500 list incomplete. Import cancelled."
        )

    return companies


def select_fact(facts, candidates, flow=False):
    gaap = facts.get("us-gaap", {})

    for tag in candidates:
        units = gaap.get(tag, {}).get("units", {})

        for unit in ("USD", "shares"):
            rows = []

            for row in units.get(unit, []):
                if row.get("form") != "10-K":
                    continue
                if not row.get("end") or not row.get("filed"):
                    continue

                if flow:
                    if not row.get("start"):
                        continue
                    try:
                        from datetime import date
                        days = (
                            date.fromisoformat(row["end"])
                            - date.fromisoformat(row["start"])
                        ).days
                    except ValueError:
                        continue
                    if not 300 <= days <= 400:
                        continue

                rows.append(row)

            if rows:
                chosen = max(
                    rows,
                    key=lambda r: (
                        r["end"],
                        r["filed"],
                    ),
                )

                return {
                    "value": chosen["val"],
                    "unit": unit,
                    "period_start": chosen.get("start"),
                    "period_end": chosen["end"],
                    "filed": chosen["filed"],
                    "accession": chosen.get("accn"),
                    "tag": tag,
                    "form": chosen["form"],
                }

    return None


def main():
    agent = os.getenv("SEC_USER_AGENT", "").strip()

    if "@" not in agent:
        raise SystemExit(
            "Configure SEC_USER_AGENT in GitHub secrets."
        )

    companies = get_companies(agent)
    OUT.mkdir(parents=True, exist_ok=True)

    index = []
    successes = 0
    failures = []

    print(f"Found {len(companies)} S&P 500 securities")

    for number, company in enumerate(companies, 1):
        ticker = company["ticker"]
        cik = company["cik"]

        try:
            url = (
                "https://data.sec.gov/api/xbrl/"
                f"companyfacts/CIK{cik}.json"
            )
            payload = json.loads(download(url, agent))
            facts = payload.get("facts", {})

            financials = {
                key: select_fact(
                    facts,
                    candidates,
                    flow=(key in FLOW_FIELDS),
                )
                for key, candidates in TAGS.items()
            }

            record = {
                "ticker": ticker,
                "cik": cik,
                "company_name": payload.get(
                    "entityName", company["name"]
                ),
                "sector": company["sector"],
                "source": url,
                "fetched_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "financials": financials,
                "review_required": True,
                "note": (
                    "SEC reported fields. Different tags and "
                    "fiscal periods require validation. "
                    "Operating cash flow minus capex is "
                    "not automatically unlevered FCFF. "
                    "Not a completed DCF valuation."
                ),
            }

            filepath = OUT / f"{ticker}.json"
            filepath.write_text(
                json.dumps(record, indent=2),
                encoding="utf-8",
            )

            index.append({
                "ticker": ticker,
                "company_name": record["company_name"],
                "sector": record["sector"],
                "path": f"data/companies/{ticker}.json",
                "review_required": True,
            })

            successes += 1
            print(f"[{number}/{len(companies)}] OK {ticker}")

        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "error": str(exc),
            })
            print(f"ERROR {ticker}: {exc}")

        time.sleep(0.35)

    Path("data/index.json").write_text(
        json.dumps(index, indent=2),
        encoding="utf-8",
    )

    Path("data/import_report.json").write_text(
        json.dumps({
            "total": len(companies),
            "downloaded": successes,
            "failures": failures,
            "finished_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    print(
        f"Finished: {successes}/{len(companies)} "
        "securities downloaded"
    )

    if successes < 450:
        raise SystemExit(
            "Too few successful downloads. Check the logs."
        )


if __name__ == "__main__":
    main()
