
"""VALTRIX - Prepare reviewed financial data for Lovable.

Combines SEC fundamentals and Alpaca IEX prices.
Does not calculate FCFF or automatically approve DCF valuations.
"""

import json
from datetime import datetime, date, timezone
from pathlib import Path

DATA = Path("data")
COMPANIES = DATA / "companies"
OUTPUT = DATA / "app"
TODAY = datetime.now(timezone.utc).date()

FIELDS = {
    "revenue": "Revenue",
    "operating_income": "Operating income",
    "operating_cash_flow": "Operating cash flow",
    "capital_expenditure": "Capital expenditure",
    "depreciation_amortization": "Depreciation and amortization",
    "income_tax_expense": "Income tax expense",
    "cash": "Cash and cash equivalents",
    "debt_current": "Current debt",
    "debt_noncurrent": "Noncurrent debt",
    "shares_outstanding": "Shares outstanding",
    "diluted_shares": "Diluted weighted-average shares",
    "accounts_receivable": "Accounts receivable",
    "inventory": "Inventory",
    "accounts_payable": "Accounts payable",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def age_days(value):
    if not value:
        return None

    try:
        day = date.fromisoformat(str(value)[:10])
        return (TODAY - day).days
    except (ValueError, TypeError):
        return None


def prepare_field(key, label, fact, sec_source):
    if not isinstance(fact, dict):
        return {
            "key": key,
            "label": label,
            "available": False,
            "value_millions": None,
            "status": "MISSING",
            "editable": True,
        }

    value = fact.get("value")
    unit = fact.get("unit")
    expected = (
        "shares" if key in (
            "shares_outstanding", "diluted_shares"
        ) else "USD"
    )

    valid_number = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )
    valid = valid_number and unit == expected

    age = age_days(fact.get("period_end"))
    freshness_limit = (
        210 if fact.get("method") == "latest_balance"
        else 500
    )

    warnings = []

    if not valid:
        warnings.append("Invalid value or unit")

    if age is None or age < 0:
        warnings.append("Invalid reporting date")
    elif age > freshness_limit:
        warnings.append("Reporting period may be stale")

    filed_age = age_days(fact.get("filed"))
    if filed_age is None or filed_age < 0:
        warnings.append("Invalid filing date")

    if fact.get("method") == "annual_fallback":
        warnings.append("Annual figure, not verified TTM")

    if key == "diluted_shares":
        warnings.append(
            "Weighted-average diluted shares need review"
        )

    return {
        "key": key,
        "label": label,
        "available": valid,
        "value_millions": (
            round(value / 1_000_000, 6)
            if valid else None
        ),
        "original_unit": unit,
        "display_unit": (
            "million shares"
            if unit == "shares" else "USD millions"
        ),
        "period_start": fact.get("period_start"),
        "period_end": fact.get("period_end"),
        "filed": fact.get("filed"),
        "method": fact.get("method"),
        "tag": fact.get("tag"),
        "accession": fact.get("accession"),
        "source": sec_source,
        "ttm_sources": fact.get("sources"),
        "status": (
            "REVIEW_REQUIRED" if valid
            else "INVALID"
        ),
        "warnings": warnings,
        "editable": True,
    }


def prepare_price(ticker, prices):
    entry = prices.get(ticker)

    if not isinstance(entry, dict):
        return {
            "available": False,
            "status": "MISSING",
        }

    value = entry.get("price")
    valid = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
        and entry.get("currency") == "USD"
    )

    age = age_days(entry.get("bar_timestamp"))
    warnings = []

    if not valid:
        warnings.append("Invalid market price")

    if age is None or age < 0:
        warnings.append("Invalid quote date")
    elif age > 7:
        warnings.append("Market price may be stale")

    return {
        "available": valid,
        "price": value if valid else None,
        "currency": entry.get("currency"),
        "timestamp": entry.get("bar_timestamp"),
        "retrieved_at": entry.get("fetched_at_utc"),
        "source": "Alpaca",
        "feed": entry.get("feed", "iex"),
        "price_type": entry.get(
            "price_type", "IEX daily bar close"
        ),
        "status": (
            "REVIEW_REQUIRED" if valid
            else "INVALID"
        ),
        "warnings": warnings,
    }


def build_company(info, prices):
    ticker = info["ticker"]
    source_path = COMPANIES / f"{ticker}.json"

    if not source_path.exists():
        return None, "SEC file not found"

    record = read_json(source_path)
    sec_source = record.get("source")
    original = record.get("financials", {})

    fields = {
        key: prepare_field(
            key, label, original.get(key), sec_source
        )
        for key, label in FIELDS.items()
    }

    missing = [
        key for key, field in fields.items()
        if not field["available"]
    ]

    sector = record.get("sector", info.get("sector"))
    financial_sector = sector == "Financials"

    warnings = list(record.get("warnings", []))

    if financial_sector:
        warnings.append(
            "Standard enterprise FCFF DCF is not appropriate "
            "without a sector-specific methodology"
        )

    warnings.append(
        "FCFF is not verified. User must supply "
        "and review the DCF cash-flow input."
    )

    company = {
        "ticker": ticker,
        "company_name": record.get(
            "company_name", info.get("company_name")
        ),
        "sector": sector,
        "currency": "USD",
        "market": prepare_price(ticker, prices),
        "financials": fields,
        "missing_fields": missing,
        "status": "MANUAL_REVIEW_REQUIRED",
        "automatic_dcf_enabled": False,
        "manual_mode_enabled": True,
        "fcff_available": False,
        "source": sec_source,
        "fetched_at_utc": record.get("fetched_at_utc"),
        "warnings": warnings,
    }

    return company, None


def main():
    index = read_json(DATA / "index.json")
    prices = read_json(DATA / "prices.json")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "companies").mkdir(
        parents=True, exist_ok=True
    )

    catalog = []
    errors = []
    price_count = 0

    for info in index:
        ticker = info["ticker"]

        try:
            company, error = build_company(info, prices)

            if error:
                errors.append({
                    "ticker": ticker,
                    "error": error,
                })
                continue

            save_json(
                OUTPUT / "companies" / f"{ticker}.json",
                company,
            )

            if company["market"]["available"]:
                price_count += 1

            catalog.append({
                "ticker": ticker,
                "company_name": company["company_name"],
                "sector": company["sector"],
                "currency": "USD",
                "market_price_available": (
                    company["market"]["available"]
                ),
                "missing_fields": company["missing_fields"],
                "automatic_dcf_enabled": False,
                "path": f"companies/{ticker}.json",
            })

        except Exception as exc:
            errors.append({
                "ticker": ticker,
                "error": str(exc),
            })

    catalog.sort(key=lambda x: x["ticker"])

    save_json(OUTPUT / "index.json", catalog)

    report = {
        "total_in_source": len(index),
        "companies_prepared": len(catalog),
        "prices_available": price_count,
        "automatic_dcf_validated": 0,
        "errors": errors,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "notes": [
            "All SEC financial values require review",
            "All monetary financial values are in USD millions",
            "Share counts are in millions",
            "Market prices are USD per share, not millions",
            "Prices are Alpaca IEX daily-bar closes",
            "FCFF is not calculated",
            "Manual company entry remains supported",
        ],
    }

    save_json(OUTPUT / "report.json", report)

    print(
        f"Prepared: {len(catalog)}/{len(index)} companies"
    )
    print(f"Prices available: {price_count}")
    print(f"Errors: {len(errors)}")

    if errors:
        raise SystemExit(
            "Some companies failed. Inspect data/app/report.json"
        )


if __name__ == "__main__":
    main()
