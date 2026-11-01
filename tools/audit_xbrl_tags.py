
"""VALTRIX - Experimental SEC XBRL tag coverage audit.

Reads company facts from SEC EDGAR and looks for recent
alternative accounting tags.

This is an audit, not an automatic tag replacement system.
Does not change production data or calculate FCFF.
"""

import json
import math
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

DATA = Path("data")
OUTPUT = DATA / "xbrl_tag_audit_report.json"

# Existing tags and additional concepts worth investigating.
# Alternatives are NOT automatically considered equivalent.

GROUPS = {
    "operating_income": {
        "existing": [
            "OperatingIncomeLoss",
        ],
        "investigate": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        ],
    },
    "capital_expenditure": {
        "existing": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ],
        "investigate": [
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForProceedsFromOtherInvestingActivities",
        ],
    },
    "depreciation_amortization": {
        "existing": [
            "DepreciationDepletionAndAmortization",
            "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
            "DepreciationAmortizationAndAccretionNet",
        ],
        "investigate": [
            "Depreciation",
            "AmortizationOfIntangibleAssets",
            "DepreciationAmortizationAndAccretionNetOfAssetsAndLiabilities",
        ],
    },
}

# Some tags are broader, narrower or economically different.
# Their presence is only a research lead.
WARNINGS = {
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
        "Pre-tax income is NOT interchangeable with operating income.",
    "PaymentsToAcquireProductiveAssets":
        "May include a different set of investments from PP&E CapEx.",
    "PaymentsForProceedsFromOtherInvestingActivities":
        "Generic investing activity is NOT a direct CapEx substitute.",
    "Depreciation":
        "Depreciation alone may exclude amortization.",
    "AmortizationOfIntangibleAssets":
        "Amortization alone is not total D&A.",
    "DepreciationAmortizationAndAccretionNetOfAssetsAndLiabilities":
        "Check accounting meaning before using as total D&A.",
}

TODAY = date.today()
MAX_PERIOD_AGE_DAYS = 500


def read_json(path):
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
    except (ValueError, TypeError):
        return None


def get_company_facts(cik, agent):
    url = (
        "https://data.sec.gov/api/xbrl/companyfacts/"
        f"CIK{str(cik).zfill(10)}.json"
    )

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
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def recent_annual_facts(facts, tag):
    rows = (
        facts.get("us-gaap", {})
        .get(tag, {})
        .get("units", {})
        .get("USD", [])
    )

    candidates = []

    for row in rows:
        if row.get("form") != "10-K":
            continue

        start = parse_date(row.get("start"))
        end = parse_date(row.get("end"))
        filed = parse_date(row.get("filed"))

        if not start or not end or not filed:
            continue

        if not valid_number(row.get("val")):
            continue

        duration = (end - start).days

        if not 330 <= duration <= 380:
            continue

        if not (start <= end <= filed <= TODAY):
            continue

        if (TODAY - end).days > MAX_PERIOD_AGE_DAYS:
            continue

        candidates.append(row)

    if not candidates:
        return None

    selected = max(
        candidates,
        key=lambda row: (
            row["end"],
            row["filed"],
        ),
    )

    return {
        "value": selected["val"],
        "unit": "USD",
        "period_start": selected["start"],
        "period_end": selected["end"],
        "filed": selected["filed"],
        "accession": selected.get("accn"),
        "form": selected["form"],
        "tag": tag,
    }


def audit_company(company, agent):
    ticker = company["ticker"]

    result = {
        "ticker": ticker,
        "sector": company.get("sector"),
        "status": "REVIEW_REQUIRED",
        "fields": {},
        "warnings": [],
    }

    if result["sector"] == "Financials":
        result["status"] = "SECTOR_REVIEW"
        result["warnings"].append(
            "Standard FCFF model requires sector-specific review"
        )
        return result

    cik = company.get("cik")

    if not cik:
        company_path = DATA / "companies" / f"{ticker}.json"

        if not company_path.exists():
            result["status"] = "ERROR"
            result["warnings"].append("Company JSON missing")
            return result

        cik = read_json(company_path).get("cik")

    if not cik:
        result["status"] = "ERROR"
        result["warnings"].append("Missing SEC CIK")
        return result

    facts = get_company_facts(cik, agent).get("facts", {})

    for field, definitions in GROUPS.items():
        existing = {}
        alternatives = {}

        for tag in definitions["existing"]:
            item = recent_annual_facts(facts, tag)
            if item:
                existing[tag] = item

        for tag in definitions["investigate"]:
            item = recent_annual_facts(facts, tag)
            if item:
                alternatives[tag] = {
                    **item,
                    "caution": WARNINGS.get(
                        tag,
                        "Requires accounting review",
                    ),
                }

        result["fields"][field] = {
            "existing_recent_tags": existing,
            "additional_tags_for_review": alternatives,
            "existing_recent_count": len(existing),
            "additional_recent_count": len(alternatives),
        }

    result["status"] = "AUDITED"
    return result


def main():
    agent = os.getenv("SEC_USER_AGENT", "").strip()

    if "@" not in agent:
        raise SystemExit("SEC_USER_AGENT must be configured")

    index = read_json(DATA / "index.json")

    results = []
    statuses = Counter()
    additional_coverage = Counter()

    for number, company in enumerate(index, 1):
        ticker = company["ticker"]

        try:
            result = audit_company(company, agent)
        except Exception as error:
            result = {
                "ticker": ticker,
                "status": "ERROR",
                "error": str(error),
            }

        results.append(result)
        statuses[result["status"]] += 1

        for field, detail in result.get("fields", {}).items():
            if detail["additional_recent_count"] > 0:
                additional_coverage[field] += 1

        print(
            f"[{number}/{len(index)}] "
            f"{ticker}: {result['status']}"
        )

        # Conservative request pacing for SEC EDGAR.
        time.sleep(0.4)

    report = {
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "total_companies": len(index),
        "status_counts": dict(statuses),
        "companies_with_additional_tags": dict(
            additional_coverage
        ),
        "companies": results,
        "limitations": [
            "Annual 10-K observations only.",
            "Alternative tags are not certified equivalents.",
            "Tags can have different accounting definitions.",
            "Company-specific extension tags are not analyzed.",
            "No missing component is replaced with zero.",
            "No FCFF has been calculated or validated.",
            "No existing financial JSON files are modified.",
            "No automated DCF valuations are enabled.",
        ],
    }

    OUTPUT.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("VALTRIX XBRL TAG AUDIT COMPLETED")
    print(f"Companies analyzed: {len(index)}")
    print("Status counts:", dict(statuses))
    print(
        "Additional tag coverage:",
        dict(additional_coverage),
    )


if __name__ == "__main__":
    main()
