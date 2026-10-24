
"""VALTRIX - Financial data quality validation.

Checks SEC data quality without modifying original data.
Does not certify that a company is DCF-ready.
"""

import json
from collections import Counter
from datetime import date
from pathlib import Path

DATA = Path("data")
COMPANIES = DATA / "companies"
REQUIRED = [
    "revenue",
    "operating_cash_flow",
    "capital_expenditure",
    "cash",
    "debt_current",
    "debt_noncurrent",
    "shares_outstanding",
]
FLOWS = REQUIRED[:3]


def read_date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def validate(record):
    ticker = record.get("ticker", "UNKNOWN")
    sector = record.get("sector", "")
    financials = record.get("financials", {})
    issues = []
    today = date.today()

    if sector == "Financials":
        issues.append("Standard FCFF DCF unsuitable for financial sector")

    for field in REQUIRED:
        fact = financials.get(field)

        if not isinstance(fact, dict):
            issues.append(f"Missing {field}")
            continue

        value = fact.get("value")

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"Invalid value: {field}")
            continue

        expected_unit = "shares" if field == "shares_outstanding" else "USD"

        if fact.get("unit") != expected_unit:
            issues.append(f"Incorrect unit: {field}")

        period = read_date(fact.get("period_end"))

        if period is None:
            issues.append(f"Missing period date: {field}")
        else:
            age = (today - period).days

            if age < 0:
                issues.append(f"Future reporting period: {field}")
            elif age > 450:
                issues.append(f"Stale data: {field}")

        filed = read_date(fact.get("filed"))

        if filed is None:
            issues.append(f"Missing filing date: {field}")
        elif filed > today:
            issues.append(f"Future filing date: {field}")
        elif period and filed < period:
            issues.append(f"Filing before period end: {field}")

        if field in ("cash", "shares_outstanding") and value <= 0:
            issues.append(f"Non-positive value: {field}")

        if field in ("debt_current", "debt_noncurrent") and value < 0:
            issues.append(f"Negative debt: {field}")

    flow_facts = [financials.get(key) for key in FLOWS]

    if all(isinstance(f, dict) for f in flow_facts):
        methods = [f.get("method") for f in flow_facts]
        periods = [f.get("period_end") for f in flow_facts]

        if any(method != "TTM" for method in methods):
            issues.append("Not all flow figures use TTM")

        if len(set(periods)) != 1:
            issues.append("Flow reporting periods do not match")

        # Our current importer does not save all three
        # source periods needed to verify the TTM formula.
        issues.append("TTM source periods require verification")

    cash = financials.get("cash")
    debt_current = financials.get("debt_current")
    debt_noncurrent = financials.get("debt_noncurrent")

    balance_facts = [cash, debt_current, debt_noncurrent]

    if all(isinstance(f, dict) for f in balance_facts):
        periods = [f.get("period_end") for f in balance_facts]
        if len(set(periods)) != 1:
            issues.append("Cash and debt reporting dates differ")

    issues.append("Unlevered FCFF not yet calculated or verified")
    issues.append("Diluted share count not yet verified")

    return {
        "ticker": ticker,
        "company_name": record.get("company_name"),
        "sector": sector,
        "status": "REVIEW_REQUIRED",
        "issues": sorted(set(issues)),
    }


def main():
    index = json.loads(
        (DATA / "index.json").read_text(encoding="utf-8")
    )

    results = []
    missing_files = []

    for company in index:
        ticker = company["ticker"]
        path = COMPANIES / f"{ticker}.json"

        if not path.exists():
            missing_files.append(ticker)
            continue

        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            results.append(validate(record))
        except (OSError, ValueError) as error:
            results.append({
                "ticker": ticker,
                "status": "REVIEW_REQUIRED",
                "issues": [f"Unreadable file: {error}"],
            })

    issue_counts = Counter(
        issue
        for result in results
        for issue in result["issues"]
    )

    report = {
        "total_in_index": len(index),
        "files_checked": len(results),
        "missing_files": missing_files,
        "dcf_ready": 0,
        "review_required": len(results),
        "note": (
            "Preliminary data quality checks only. "
            "No company is certified DCF-ready until "
            "TTM source periods, FCFF and diluted "
            "shares are verified."
        ),
        "issue_counts": dict(issue_counts),
        "companies": results,
    }

    output = DATA / "validation_report.json"
    output.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(f"Files checked: {len(results)}")
    print(f"Missing files: {len(missing_files)}")
    print("Validation report generated.")

    if missing_files:
        raise SystemExit("Some financial files are missing.")


if __name__ == "__main__":
    main()
