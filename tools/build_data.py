
"""VALTRIX - SEC fundamentals with TTM and latest balance sheet data."""

import csv
import io
import json
import os
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

LIST_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
OUT = Path("data/companies")

TAGS = {
    
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "DepreciationAmortizationAndAccretionNet",
    ],
    "income_tax_expense": [
        "IncomeTaxExpenseBenefit",
    ],

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
        "LongTermDebtAndCapitalLeaseObligationsCurrent"
    ],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations"
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding"
    ],
}

FLOW_KEYS = (
    "revenue",
    "operating_cash_flow",
    "capital_expenditure",
    "operating_income",
    "depreciation_amortization",
    "income_tax_expense",
)


def get_bytes(url, agent):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": agent,
            "Accept": "application/json, text/csv",
            "Accept-Encoding": "identity",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def get_companies(agent):
    data = get_bytes(LIST_URL, agent)
    rows = csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"))
    )
    companies = []
    for row in rows:
        if row.get("Symbol") and row.get("CIK"):
            companies.append({
                "ticker": row["Symbol"].strip(),
                "cik": str(int(row["CIK"])).zfill(10),
                "name": row["Security"],
                "sector": row.get("GICS Sector", ""),
            })
    if len(companies) < 450:
        raise RuntimeError("Incomplete S&P 500 list")
    return companies


def duration(row):
    try:
        return (
            date.fromisoformat(row["end"])
            - date.fromisoformat(row["start"])
        ).days
    except (KeyError, ValueError, TypeError):
        return None


def rows_for_tag(facts, tag, unit):
    return facts.get("us-gaap", {}).get(
        tag, {}
    ).get("units", {}).get(unit, [])


def point_in_time(facts, tags, unit):
    candidates = []
    for tag in tags:
        for row in rows_for_tag(facts, tag, unit):
            if (
                row.get("form") in ("10-K", "10-Q")
                and row.get("end")
                and row.get("filed")
                and not row.get("start")
            ):
                candidates.append((row, tag))

    if not candidates:
        return None

    row, tag = max(
        candidates,
        key=lambda item: (
            item[0]["end"],
            item[0]["filed"],
        ),
    )
    return {
        "value": row["val"],
        "unit": unit,
        "period_end": row["end"],
        "filed": row["filed"],
        "tag": tag,
        "form": row["form"],
        "method": "latest_reported_balance",
    }


def latest_annual(facts, tags):
    candidates = []
    for tag in tags:
        for row in rows_for_tag(facts, tag, "USD"):
            days = duration(row)
            if (
                row.get("form") == "10-K"
                and days is not None
                and 300 <= days <= 400
                and row.get("filed")
            ):
                candidates.append((row, tag))

    if not candidates:
        return None

    row, tag = max(
        candidates,
        key=lambda item: (
            item[0]["end"],
            item[0]["filed"],
        ),
    )
    return {
        "value": row["val"],
        "unit": "USD",
        "period_start": row["start"],
        "period_end": row["end"],
        "filed": row["filed"],
        "tag": tag,
        "form": row["form"],
        "method": "annual",
    }


def latest_ttm(facts, tags):
    annual = latest_annual(facts, tags)
    if annual is None:
        return None

    quarterly = []
    for tag in tags:
        for row in rows_for_tag(facts, tag, "USD"):
            days = duration(row)
            if (
                row.get("form") == "10-Q"
                and days is not None
                and 60 <= days <= 310
                and row.get("filed")
            ):
                quarterly.append((row, tag, days))

    recent = [
        item for item in quarterly
        if item[0]["end"] > annual["period_end"]
    ]

    if not recent:
        return annual

    # Prefer the latest quarter's longest YTD period.
    latest_end = max(item[0]["end"] for item in recent)
    candidates = [
        item for item in recent
        if item[0]["end"] == latest_end
    ]
    current, current_tag, current_days = max(
        candidates,
        key=lambda item: (item[2], item[0]["filed"]),
    )

    # Comparable YTD period from the preceding fiscal year.
    previous = [
        item for item in quarterly
        if (
            item[1] == current_tag
            and item[0]["end"] <= annual["period_end"]
            and abs(item[2] - current_days) <= 10
            and 330 <= (
                date.fromisoformat(current["end"])
                - date.fromisoformat(item[0]["end"])
            ).days <= 400
        )
    ]

    # The annual and quarterly data must use the same tag.
    if not previous or annual["tag"] != current_tag:
        result = annual.copy()
        result["method"] = "annual_fallback"
        result["warning"] = "TTM unavailable; annual data used"
        return result

    previous_row = max(
        previous,
        key=lambda item: (
            item[0]["end"], item[0]["filed"]
        ),
    )[0]

    ttm_value = (
        annual["value"]
        + current["val"]
        - previous_row["val"]
    )

    return {
        "value": ttm_value,
        "unit": "USD",
        "period_end": current["end"],
        "filed": current["filed"],
        "tag": current_tag,
        "form": "10-K + 10-Q",
        "method": "TTM",
        "formula": "FY + current YTD - prior-year YTD",
        "annual_fy_end": annual["period_end"],
    }



def diluted_shares(facts):
    tags = [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ]
    candidates = []

    for tag in tags:
        for row in rows_for_tag(facts, tag, "shares"):
            days = duration(row)
            if (
                row.get("form") == "10-K"
                and days is not None
                and 300 <= days <= 400
                and row.get("filed")
            ):
                candidates.append((row, tag))

    if not candidates:
        return None

    row, tag = max(
        candidates,
        key=lambda item: (
            item[0]["end"],
            item[0]["filed"],
        ),
    )

    return {
        "value": row["val"],
        "unit": "shares",
        "period_end": row["end"],
        "filed": row["filed"],
        "tag": tag,
        "form": "10-K",
        "method": "annual_diluted_weighted_average",
        "review_required": True,
    }

def build_record(company, agent):
    cik = company["cik"]
    url = (
        "https://data.sec.gov/api/xbrl/companyfacts/"
        f"CIK{cik}.json"
    )
    payload = json.loads(get_bytes(url, agent))
    facts = payload.get("facts", {})

    financials = {}
    warnings = []
    financials["diluted_shares"] = diluted_shares(facts)

    for key, tags in TAGS.items():
        if key in FLOW_KEYS:
            value = latest_ttm(facts, tags)
        else:
            unit = (
                "shares" if key == "shares_outstanding"
                else "USD"
            )
            value = point_in_time(facts, tags, unit)

        financials[key] = value

        if value is None:
            warnings.append(f"Missing field: {key}")
        elif value.get("method") == "annual_fallback":
            warnings.append(f"TTM unavailable: {key}")

    # These reported fields are not automatically DCF-ready.
    warnings.append(
        "Operating cash flow minus CapEx is not unlevered FCFF"
    )
    warnings.append(
        "Shares outstanding are not necessarily diluted shares"
    )
    warnings.append(
        "Debt and cash require classification and date checks"
    )

    if company["sector"] == "Financials":
        warnings.append(
            "Financial company: standard FCFF DCF unsuitable"
        )

    return {
        "ticker": company["ticker"],
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
        "warnings": warnings,
    }


def main():
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if "@" not in agent:
        raise SystemExit("Missing SEC_USER_AGENT")

    companies = get_companies(agent)
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    failures = []
    ttm_counts = {}

    for number, company in enumerate(companies, 1):
        ticker = company["ticker"]
        try:
            record = build_record(company, agent)
            (OUT / f"{ticker}.json").write_text(
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
            ttm_counts[ticker] = sum(
                record["financials"].get(key) is not None
                and record["financials"][key].get("method") == "TTM"
                for key in FLOW_KEYS
            )
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
            "downloaded": len(index),
            "failures": failures,
            "companies_with_all_three_ttm_flows": sum(
                count == 3 for count in ttm_counts.values()
            ),
            "finished_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    print(f"Finished: {len(index)}/{len(companies)}")
    if len(index) < 450:
        raise SystemExit("Too many SEC download failures")


if __name__ == "__main__":
    main()
