"""VALTRIX - Pilot XBRL financial component recovery.

Research only: MSFT, NVDA and AMZN.
Does not modify existing financial data.
Does not calculate or approve FCFF.
"""

import json
import math
import os
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

COMPANIES = {
    "MSFT": {
        "field": "depreciation_amortization",
        "tags": [
            "DepreciationDepletionAndAmortization",
            "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
            "DepreciationAmortizationAndAccretionNet",
            "Depreciation",
            "AmortizationOfIntangibleAssets",
        ],
    },
    "NVDA": {
        "field": "capital_expenditure",
        "tags": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ],
    },
    "AMZN": {
        "field": "capital_expenditure",
        "tags": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ],
    },
}

OUTPUT = Path("data/pilot_xbrl_recovery_report.json")
TODAY = date.today()
MAX_AGE_DAYS = 500


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def valid_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def fetch_sec(cik, agent):
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
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def latest_annual(facts, tag):
    rows = (
        facts.get("us-gaap", {})
        .get(tag, {})
        .get("units", {})
        .get("USD", [])
    )

    valid = []

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

        if not 330 <= (end - start).days <= 380:
            continue

        if not start <= end <= filed <= TODAY:
            continue

        if (TODAY - end).days > MAX_AGE_DAYS:
            continue

        valid.append(row)

    if not valid:
        return None

    row = max(
        valid,
        key=lambda x: (x["end"], x["filed"]),
    )

    return {
        "value_usd": row["val"],
        "period_start": row["start"],
        "period_end": row["end"],
        "filed": row["filed"],
        "accession": row.get("accn"),
        "form": row["form"],
    }


def investigate(ticker, config, agent):
    original = read_json(
        Path("data/companies") / f"{ticker}.json"
    )

    cik = original["cik"]
    facts = fetch_sec(cik, agent).get("facts", {})

    observations = {}

    for tag in config["tags"]:
        observation = latest_annual(facts, tag)

        if observation is not None:
            observations[tag] = observation

    periods = sorted({
        (
            item["period_start"],
            item["period_end"],
        )
        for item in observations.values()
    })

    issues = []

    if not observations:
        issues.append(
            "No recent annual observations found"
        )

    if len(periods) > 1:
        issues.append(
            "Tags contain different annual periods"
        )

    if ticker == "MSFT":
        issues.append(
            "Depreciation and intangible amortization "
            "may overlap with reported total D&A."
        )
        issues.append(
            "Do not add components until accounting "
            "definitions and scope are verified."
        )

    if ticker in ("NVDA", "AMZN"):
        issues.append(
            "PaymentsToAcquireProductiveAssets may "
            "have a different accounting scope from "
            "PP&E capital expenditure."
        )
        issues.append(
            "Review the SEC cash flow statement and "
            "footnotes before selecting a CapEx figure."
        )

    return {
        "ticker": ticker,
        "field": config["field"],
        "cik": cik,
        "source": (
            "https://data.sec.gov/api/xbrl/"
            f"companyfacts/CIK{str(cik).zfill(10)}.json"
        ),
        "observations_by_tag": observations,
        "same_annual_period": (
            len(periods) == 1 and bool(observations)
        ),
        "periods_found": [
            {"start": start, "end": end}
            for start, end in periods
        ],
        "issues": issues,
        "review_status": "MANUAL_REVIEW_REQUIRED",
        "fcff_approved": False,
    }


def main():
    agent = os.environ.get(
        "SEC_USER_AGENT", ""
    ).strip()

    if "@" not in agent:
        raise SystemExit(
            "SEC_USER_AGENT is missing"
        )

    results = []
    errors = []

    for ticker, config in COMPANIES.items():
        print(f"Auditing {ticker}...")

        try:
            result = investigate(
                ticker, config, agent
            )
            results.append(result)
            print(f"{ticker}: AUDITED")

        except Exception as error:
            errors.append({
                "ticker": ticker,
                "error": str(error),
            })
            print(f"{ticker}: ERROR - {error}")

        time.sleep(0.5)

    report = {
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "pilot_companies": list(COMPANIES),
        "results": results,
        "errors": errors,
        "fcff_calculated": 0,
        "fcff_approved": 0,
        "automatic_dcf_enabled": False,
        "notes": [
            "Experimental XBRL recovery only.",
            "Annual 10-K periods only.",
            "Alternative tags are not automatically equivalent.",
            "No depreciation components are automatically added.",
            "No CapEx alternatives are automatically accepted.",
            "No production financial data is modified.",
            "Manual review is required before any FCFF."
        ],
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("VALTRIX XBRL RECOVERY PILOT COMPLETE")
    print(f"Companies audited: {len(results)}")
    print(f"Errors: {len(errors)}")
    print("No FCFF generated.")

    if errors:
        raise SystemExit(
            "Pilot completed with errors; see report"
        )


if __name__ == "__main__":
    main()
