
"""VALTRIX - SEC financial data importer.

Uses verified TTM periods where available.
Keeps financial sources and review warnings.
Does not calculate or certify FCFF.
"""

import csv
import io
import json
import os
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

INDEX_URL = (
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
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
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
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
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
        "CommonStockSharesOutstanding",
    ],
    "diluted_shares": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "accounts_receivable": [
        "AccountsReceivableNetCurrent",
        "AccountsNotesAndOtherReceivablesNetCurrent",
    ],
    "inventory": [
        "InventoryNet",
    ],
    "accounts_payable": [
        "AccountsPayableCurrent",
    ],
}

FLOWS = {
    "revenue",
    "operating_cash_flow",
    "capital_expenditure",
    "operating_income",
    "depreciation_amortization",
    "income_tax_expense",
    "diluted_shares",
}

CORE = {
    "operating_cash_flow",
    "capital_expenditure",
    "operating_income",
    "cash",
    "shares_outstanding",
}


def get_bytes(url, agent):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": agent,
            "Accept-Encoding": "identity",
        },
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request, timeout=60
            ) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def companies(agent):
    data = get_bytes(INDEX_URL, agent)
    reader = csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"))
    )

    items = []

    for row in reader:
        if not row.get("Symbol") or not row.get("CIK"):
            continue

        items.append({
            "ticker": row["Symbol"].strip(),
            "cik": str(int(row["CIK"])).zfill(10),
            "name": row["Security"],
            "sector": row.get("GICS Sector", ""),
        })

    if len(items) < 450:
        raise ValueError("Incomplete constituents list")

    return items


def days(row):
    try:
        return (
            date.fromisoformat(row["end"])
            - date.fromisoformat(row["start"])
        ).days
    except (KeyError, ValueError, TypeError):
        return None


def entries(facts, tag, unit):
    return (
        facts.get("us-gaap", {})
        .get(tag, {})
        .get("units", {})
        .get(unit, [])
    )


def ref(row, tag, unit):
    return {
        "value": row["val"],
        "unit": unit,
        "period_start": row.get("start"),
        "period_end": row["end"],
        "filed": row["filed"],
        "form": row["form"],
        "accession": row.get("accn"),
        "tag": tag,
    }


def choose_balance(facts, tags, unit):
    found = []

    for tag in tags:
        for row in entries(facts, tag, unit):
            if (
                row.get("form") in ("10-K", "10-Q")
                and row.get("filed")
                and row.get("end")
                and not row.get("start")
            ):
                found.append((row, tag))

    if not found:
        return None

    row, tag = max(
        found,
        key=lambda item: (
            item[0]["end"],
            item[0]["filed"],
        ),
    )

    return {
        **ref(row, tag, unit),
        "method": "latest_balance",
    }


def choose_flow(facts, tags, unit):
    """Calculate TTM only with comparable fiscal periods."""
    candidates = []

    for tag in tags:
        raw = [
            row for row in entries(facts, tag, unit)
            if (
                row.get("form") in ("10-K", "10-Q")
                and row.get("filed")
                and row.get("end")
                and row.get("start")
            )
        ]

        annual_rows = [
            row for row in raw
            if (
                row["form"] == "10-K"
                and days(row) is not None
                and 330 <= days(row) <= 380
            )
        ]

        if not annual_rows:
            continue

        annual = max(
            annual_rows,
            key=lambda row: (
                row["end"],
                row["filed"],
            ),
        )

        annual_end = date.fromisoformat(annual["end"])

        current_rows = [
            row for row in raw
            if (
                row["form"] == "10-Q"
                and days(row) is not None
                and 60 <= days(row) <= 310
                and date.fromisoformat(row["end"]) > annual_end
                and 0 < (
                    date.fromisoformat(row["end"]) - annual_end
                ).days <= 310
            )
        ]

        if current_rows:
            current = max(
                current_rows,
                key=lambda row: (
                    row["end"],
                    days(row),
                    row["filed"],
                ),
            )

            current_start = date.fromisoformat(
                current["start"]
            )

            if abs((current_start - annual_end).days) <= 10:
                prior_rows = [
                    row for row in raw
                    if (
                        row["form"] in ("10-K", "10-Q")
                        and days(row) is not None
                        and abs(days(row) - days(current)) <= 10
                        and 350 <= (
                            date.fromisoformat(current["end"])
                            - date.fromisoformat(row["end"])
                        ).days <= 380
                        and abs((
                            date.fromisoformat(row["start"])
                            - date.fromisoformat(annual["start"])
                        ).days) <= 10
                    )
                ]

                if prior_rows:
                    prior = max(
                        prior_rows,
                        key=lambda row: (
                            row["end"],
                            row["filed"],
                        ),
                    )

                    ttm = (
                        annual["val"]
                        + current["val"]
                        - prior["val"]
                    )

                    candidates.append({
                        **ref(current, tag, unit),
                        "value": ttm,
                        "method": "TTM",
                        "formula": (
                            "annual + current_ytd - prior_ytd"
                        ),
                        "sources": {
                            "annual": ref(annual, tag, unit),
                            "current_ytd": ref(current, tag, unit),
                            "prior_ytd": ref(prior, tag, unit),
                        },
                    })
                    continue

        candidates.append({
            **ref(annual, tag, unit),
            "method": "annual_fallback",
            "warning": (
                "TTM not verifiable from comparable periods"
            ),
        })

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            item["period_end"],
            item["method"] == "TTM",
            item["filed"],
        ),
    )


def build(company, agent):
    cik = company["cik"]

    url = (
        "https://data.sec.gov/api/xbrl/companyfacts/"
        f"CIK{cik}.json"
    )

    payload = json.loads(get_bytes(url, agent))
    facts = payload.get("facts", {})

    financials = {}
    warnings = []
    today = date.today()

    for key, tags in TAGS.items():
        unit = (
            "shares"
            if key in ("shares_outstanding", "diluted_shares")
            else "USD"
        )

        if key in FLOWS:
            value = choose_flow(facts, tags, unit)
        else:
            value = choose_balance(facts, tags, unit)

        financials[key] = value

        if value is None:
            warnings.append(f"Missing: {key}")
            continue

        if value["method"] == "annual_fallback":
            warnings.append(f"Annual fallback: {key}")

        age = (
            today - date.fromisoformat(value["period_end"])
        ).days

        if age < 0 or age > 450:
            warnings.append(
                f"Out-of-range or stale period: {key}"
            )

        if date.fromisoformat(value["filed"]) > today:
            warnings.append(f"Future filing date: {key}")

    if company["sector"] == "Financials":
        warnings.append(
            "Financial sector: standard FCFF DCF unsuitable"
        )

    warnings.extend([
        "FCFF not calculated or validated",
        "Debt, leases and diluted shares require review",
        "Working-capital change requires validation",
    ])

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
        "missing_core_fields": sorted(
            key for key in CORE
            if financials[key] is None
        ),
        "import_status": "REVIEW_REQUIRED",
        "auto_dcf_ready": False,
        "warnings": warnings,
    }


def main():
    agent = os.environ.get(
        "SEC_USER_AGENT", ""
    ).strip()

    if "@" not in agent:
        raise SystemExit("SEC_USER_AGENT missing")

    items = companies(agent)
    OUT.mkdir(parents=True, exist_ok=True)

    index = []
    failed = []
    ttm_counts = {
        key: 0 for key in FLOWS
    }

    for position, company in enumerate(items, 1):
        try:
            record = build(company, agent)
            ticker = company["ticker"]

            (OUT / f"{ticker}.json").write_text(
                json.dumps(record, indent=2),
                encoding="utf-8",
            )

            for key in FLOWS:
                fact = record["financials"][key]
                if (
                    fact is not None
                    and fact["method"] == "TTM"
                ):
                    ttm_counts[key] += 1

            index.append({
                "ticker": ticker,
                "company_name": record["company_name"],
                "sector": record["sector"],
                "path": f"data/companies/{ticker}.json",
                "import_status": record["import_status"],
                "missing_core_fields": (
                    record["missing_core_fields"]
                ),
            })

            print(
                f"{position}/{len(items)} OK {ticker}"
            )

        except Exception as error:
            failed.append({
                "ticker": company["ticker"],
                "error": str(error),
            })
            print(
                f"FAILED {company['ticker']}: {error}"
            )

        time.sleep(0.35)

    Path("data/index.json").write_text(
        json.dumps(index, indent=2),
        encoding="utf-8",
    )

    report = {
        "total": len(items),
        "downloaded": len(index),
        "failures": failed,
        "ttm_coverage": ttm_counts,
        "dcf_automatically_validated": 0,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    Path("data/import_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    if len(index) < 450:
        raise SystemExit(
            "Too many failed company downloads"
        )


if __name__ == "__main__":
    main()

