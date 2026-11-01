
"""VALTRIX - Conservative historical FCFF coverage audit.

Checks data availability, freshness, fiscal-period alignment,
TTM source structure and preliminary NWC balance dates.

Does NOT calculate FCFF or enable automatic valuations.
"""

import json
import math
from collections import Counter
from datetime import date
from pathlib import Path

DATA = Path("data")
REPORT = DATA / "fcff_coverage_report.json"

CORE = (
    "operating_income",
    "depreciation_amortization",
    "capital_expenditure",
)

ADDITIONAL = (
    "revenue",
    "income_tax_expense",
    "operating_cash_flow",
)

ALL_FIELDS = CORE + ADDITIONAL
TODAY = date.today()
MAX_AGE_DAYS = 500


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def valid_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def source_valid(row):
    if not isinstance(row, dict):
        return False

    start = parse_date(row.get("period_start"))
    end = parse_date(row.get("period_end"))
    filed = parse_date(row.get("filed"))

    return (
        valid_number(row.get("value"))
        and row.get("unit") == "USD"
        and start is not None
        and end is not None
        and filed is not None
        and start <= end
        and end <= filed <= TODAY
    )


def flow_signature(fact):
    """Return a comparable fiscal-period signature or a reason."""

    if not source_valid(fact):
        return None, "Invalid value, unit or source dates"

    method = fact.get("method")
    start = parse_date(fact["period_start"])
    end = parse_date(fact["period_end"])

    if (TODAY - end).days > MAX_AGE_DAYS:
        return None, "Financial period is stale"

    if method == "annual_fallback":
        duration = (end - start).days

        if not 330 <= duration <= 380:
            return None, "Annual period is not approximately one year"

        return ("ANNUAL", start.isoformat(), end.isoformat()), None

    if method != "TTM":
        return None, "Unknown financial period method"

    sources = fact.get("sources")

    if not isinstance(sources, dict):
        return None, "TTM source breakdown is missing"

    annual = sources.get("annual")
    current = sources.get("current_ytd")
    prior = sources.get("prior_ytd")

    if not all(source_valid(x) for x in (annual, current, prior)):
        return None, "TTM source values or dates are invalid"

    a_start = parse_date(annual["period_start"])
    a_end = parse_date(annual["period_end"])
    c_start = parse_date(current["period_start"])
    c_end = parse_date(current["period_end"])
    p_start = parse_date(prior["period_start"])
    p_end = parse_date(prior["period_end"])

    annual_days = (a_end - a_start).days
    current_days = (c_end - c_start).days
    prior_days = (p_end - p_start).days

    if not 330 <= annual_days <= 380:
        return None, "TTM annual base is not a full fiscal year"

    if not 60 <= current_days <= 310:
        return None, "TTM current YTD has unexpected duration"

    if abs(current_days - prior_days) > 10:
        return None, "TTM YTD periods have different durations"

    if not 350 <= (c_end - p_end).days <= 380:
        return None, "TTM YTD end dates are not one year apart"

    if abs((p_start - a_start).days) > 10:
        return None, "Prior YTD start does not match annual base"

    if abs((c_start - a_end).days) > 10:
        return None, "Current YTD does not follow annual base"

    expected = (
        annual["value"] + current["value"] - prior["value"]
    )

    tolerance = max(0.01, abs(expected) * 1e-9)

    if abs(fact["value"] - expected) > tolerance:
        return None, "TTM arithmetic does not reconcile"

    # These source dates identify the same TTM construction.
    signature = (
        "TTM",
        a_start.isoformat(),
        a_end.isoformat(),
        c_start.isoformat(),
        c_end.isoformat(),
        p_start.isoformat(),
        p_end.isoformat(),
    )

    return signature, None


def check_nwc(ticker, reference_end):
    path = DATA / "dcf_inputs" / f"{ticker}.json"

    if not path.exists():
        return False, "NWC file unavailable"

    item = load(path)

    if item.get("status") != "PRELIMINARY":
        return False, "NWC is not preliminary-complete"

    change = item.get("simplified_change_in_nwc")

    if not valid_number(change):
        return False, "NWC change is invalid"

    current = item.get("current", {})
    previous = item.get("previous", {})

    if not isinstance(current, dict) or not isinstance(previous, dict):
        return False, "NWC balance details are missing"

    fields = (
        "accounts_receivable",
        "inventory",
        "accounts_payable",
    )

    current_dates = set()
    previous_dates = set()

    for field in fields:
        cur = current.get(field)
        prev = previous.get(field)

        if not isinstance(cur, dict) or not isinstance(prev, dict):
            return False, f"Missing NWC component: {field}"

        if not (
            valid_number(cur.get("value"))
            and valid_number(prev.get("value"))
        ):
            return False, f"Invalid NWC component: {field}"

        if cur.get("unit") != "USD" or prev.get("unit") != "USD":
            return False, f"Wrong NWC units: {field}"

        current_dates.add(cur.get("period_end"))
        previous_dates.add(prev.get("period_end"))

    if len(current_dates) != 1 or len(previous_dates) != 1:
        return False, "NWC balance dates do not align"

    current_date = parse_date(next(iter(current_dates)))
    previous_date = parse_date(next(iter(previous_dates)))

    if current_date is None or previous_date is None:
        return False, "Invalid NWC dates"

    if current_date != reference_end:
        return False, "NWC date differs from core financial period"

    if not 350 <= (current_date - previous_date).days <= 380:
        return False, "NWC balances are not one year apart"

    return True, "Simplified NWC available; accounting review required"


def audit(info):
    ticker = info["ticker"]
    record_path = DATA / "companies" / f"{ticker}.json"

    result = {
        "ticker": ticker,
        "sector": info.get("sector"),
        "status": "INSUFFICIENT_DATA",
        "available_fields": [],
        "missing_fields": [],
        "period_issues": {},
        "nwc_status": "NOT_CHECKED",
        "issues": [],
    }

    if not record_path.exists():
        result["issues"].append("SEC company JSON not found")
        return result

    record = load(record_path)
    sector = record.get("sector", info.get("sector"))
    result["sector"] = sector

    if sector == "Financials":
        result["status"] = "SECTOR_REVIEW"
        result["issues"].append(
            "Standard enterprise FCFF DCF may be unsuitable"
        )
        return result

    financials = record.get("financials", {})
    signatures = {}

    for field in ALL_FIELDS:
        fact = financials.get(field)

        if not isinstance(fact, dict):
            result["missing_fields"].append(field)
            result["period_issues"][field] = "Missing SEC fact"
            continue

        signature, problem = flow_signature(fact)

        if signature is None:
            result["missing_fields"].append(field)
            result["period_issues"][field] = problem
        else:
            result["available_fields"].append(field)
            signatures[field] = signature

    core_ready = all(field in signatures for field in CORE)

    if not core_ready:
        result["issues"].append(
            "One or more essential FCFF components are unusable"
        )

    aligned = (
        core_ready
        and len({signatures[field] for field in CORE}) == 1
    )

    if core_ready and not aligned:
        result["issues"].append(
            "Essential FCFF components use incompatible fiscal periods"
        )

    if aligned:
        signature = signatures[CORE[0]]
        period_end = parse_date(
            signature[2] if signature[0] == "ANNUAL"
            else signature[4]
        )

        nwc_ok, nwc_message = check_nwc(ticker, period_end)

        result["nwc_status"] = (
            "PRELIMINARY" if nwc_ok else "REVIEW_REQUIRED"
        )
        result["issues"].append(nwc_message)
    else:
        nwc_ok = False
        result["nwc_status"] = "NOT_ASSESSED"

    if aligned and nwc_ok:
        result["status"] = "CANDIDATE_FOR_REVIEW"
    elif result["available_fields"]:
        result["status"] = "REVIEW_REQUIRED"

    result["issues"].append(
        "Normalized operating tax rate not established"
    )
    result["issues"].append(
        "Simplified NWC omits other operating components"
    )
    result["issues"].append(
        "No historical FCFF calculated or validated"
    )

    return result


def main():
    index = load(DATA / "index.json")
    results = []
    statuses = Counter()
    coverage = Counter()

    for info in index:
        try:
            result = audit(info)
        except (OSError, ValueError, TypeError, KeyError) as error:
            result = {
                "ticker": info.get("ticker"),
                "status": "ERROR",
                "issues": [str(error)],
            }

        results.append(result)
        statuses[result["status"]] += 1

        for field in result.get("available_fields", []):
            coverage[field] += 1

    report = {
        "total": len(index),
        "status_counts": dict(statuses),
        "field_coverage": dict(coverage),
        "fcff_calculated": 0,
        "fcff_validated": 0,
        "automatic_dcf_enabled": False,
        "notes": [
            "Strict coverage audit only.",
            "TTM components checked against underlying SEC sources.",
            "Annual and TTM figures cannot be mixed.",
            "Stale or incompatible components are excluded.",
            "NWC dates are checked independently.",
            "Preliminary NWC is not a complete reinvestment measure.",
            "No historical FCFF was calculated.",
            "No production JSON was changed.",
        ],
        "companies": results,
    }

    REPORT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("VALTRIX - STRICT FCFF COVERAGE AUDIT")
    print(f"Companies analyzed: {len(index)}")

    for status, count in sorted(statuses.items()):
        print(f"{status}: {count}")

    print("No FCFF calculated or approved.")


if __name__ == "__main__":
    main()

