
"""VALTRIX - Historical FCFF data coverage audit.

Reads existing SEC company JSON files and preliminary NWC files.
Does not calculate FCFF, modify company data, or enable DCF.
"""

import json
from collections import Counter
from pathlib import Path

DATA = Path("data")
COMPANIES = DATA / "companies"
DCF_INPUTS = DATA / "dcf_inputs"
REPORT = DATA / "fcff_coverage_report.json"

REQUIRED_FLOWS = (
    "operating_income",
    "depreciation_amortization",
    "capital_expenditure",
)

OPTIONAL_FIELDS = (
    "revenue",
    "income_tax_expense",
    "operating_cash_flow",
)

ALL_FIELDS = REQUIRED_FLOWS + OPTIONAL_FIELDS


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def usable_fact(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("value"), (int, float))
        and not isinstance(value.get("value"), bool)
        and value.get("unit") == "USD"
        and bool(value.get("period_end"))
        and value.get("method") in ("TTM", "annual_fallback")
    )


def audit_company(info):
    ticker = info["ticker"]
    path = COMPANIES / f"{ticker}.json"

    result = {
        "ticker": ticker,
        "sector": info.get("sector"),
        "status": "INSUFFICIENT_DATA",
        "available_fields": [],
        "missing_fields": [],
        "flow_periods": {},
        "nwc_status": "NOT_AVAILABLE",
        "issues": [],
    }

    if not path.exists():
        result["issues"].append("Missing SEC company JSON")
        return result

    record = read_json(path)
    financials = record.get("financials", {})
    sector = record.get("sector", info.get("sector"))

    result["sector"] = sector

    if sector == "Financials":
        result["status"] = "SECTOR_REVIEW"
        result["issues"].append(
            "Standard enterprise FCFF DCF may be unsuitable"
        )
        return result

    for field in ALL_FIELDS:
        fact = financials.get(field)

        if usable_fact(fact):
            result["available_fields"].append(field)
            result["flow_periods"][field] = {
                "start": fact.get("period_start"),
                "end": fact.get("period_end"),
                "method": fact.get("method"),
                "filed": fact.get("filed"),
            }
        else:
            result["missing_fields"].append(field)

    nwc_path = DCF_INPUTS / f"{ticker}.json"

    if nwc_path.exists():
        nwc = read_json(nwc_path)

        if (
            nwc.get("status") == "PRELIMINARY"
            and isinstance(
                nwc.get("simplified_change_in_nwc"),
                (int, float),
            )
            and not isinstance(
                nwc.get("simplified_change_in_nwc"), bool
            )
        ):
            result["nwc_status"] = "PRELIMINARY"
        else:
            result["nwc_status"] = "REVIEW_REQUIRED"

    missing_required = [
        field for field in REQUIRED_FLOWS
        if field in result["missing_fields"]
    ]

    if missing_required:
        result["issues"].append(
            "Missing essential FCFF components: "
            + ", ".join(missing_required)
        )

    periods = [
        result["flow_periods"][field]
        for field in REQUIRED_FLOWS
        if field in result["flow_periods"]
    ]

    aligned = (
        len(periods) == len(REQUIRED_FLOWS)
        and len({
            (p["start"], p["end"], p["method"])
            for p in periods
        }) == 1
    )

    if not aligned:
        result["issues"].append(
            "Core flow periods or methods do not match"
        )

    if result["nwc_status"] != "PRELIMINARY":
        result["issues"].append(
            "Simplified working-capital change unavailable"
        )
    else:
        result["issues"].append(
            "NWC is simplified and requires accounting review"
        )

    if aligned and result["nwc_status"] == "PRELIMINARY":
        result["status"] = "CANDIDATE_FOR_REVIEW"
    elif result["available_fields"]:
        result["status"] = "REVIEW_REQUIRED"

    result["issues"].append(
        "A normalized tax assumption has not been established"
    )
    result["issues"].append(
        "No FCFF has been calculated or validated"
    )

    return result


def main():
    index = read_json(DATA / "index.json")
    results = []
    counts = Counter()
    field_coverage = Counter()

    for info in index:
        try:
            result = audit_company(info)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result = {
                "ticker": info.get("ticker"),
                "status": "ERROR",
                "issues": [str(exc)],
            }

        results.append(result)
        counts[result["status"]] += 1

        for field in result.get("available_fields", []):
            field_coverage[field] += 1

    report = {
        "total": len(index),
        "status_counts": dict(counts),
        "field_coverage": dict(field_coverage),
        "fcff_calculated": 0,
        "fcff_validated": 0,
        "automatic_dcf_enabled": False,
        "notes": [
            "Coverage audit only; no FCFF is calculated.",
            "Candidates require tax and accounting review.",
            "TTM and annual periods must not be mixed.",
            "Simplified NWC excludes other operating items.",
            "NWC balance dates require further alignment.",
            "The audit does not alter production app JSON.",
        ],
        "companies": results,
    }

    REPORT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("VALTRIX FCFF COVERAGE AUDIT")
    print(f"Companies analyzed: {len(index)}")

    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")

    print("No FCFF calculated or automatically approved.")


if __name__ == "__main__":
    main()
