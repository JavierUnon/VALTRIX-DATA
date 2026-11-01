
"""VALTRIX - Conservative XBRL recovery validation.

Validates provenance, fiscal dates and reported observations
from the three-company pilot.

Does NOT certify accounting equivalence, calculate FCFF,
or change production financial data.
"""

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

INPUT = Path("data/pilot_xbrl_recovery_report.json")
OUTPUT = Path("data/xbrl_recovery_validation_report.json")

EXPECTED = {
    "MSFT": {
        "field": "depreciation_amortization",
        "cik": "0000789019",
        "tags": {
            "Depreciation",
            "AmortizationOfIntangibleAssets",
        },
    },
    "NVDA": {
        "field": "capital_expenditure",
        "cik": "0001045810",
        "tags": {
            "PaymentsToAcquireProductiveAssets",
        },
    },
    "AMZN": {
        "field": "capital_expenditure",
        "cik": "0001018724",
        "tags": {
            "PaymentsToAcquireProductiveAssets",
        },
    },
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def valid_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_observation(observation):
    issues = []

    if not isinstance(observation, dict):
        return ["Invalid SEC observation"]

    value = observation.get("value_usd")

    if not valid_number(value):
        issues.append("Invalid financial value")

    start = parse_date(observation.get("period_start"))
    end = parse_date(observation.get("period_end"))
    filed = parse_date(observation.get("filed"))

    if not all((start, end, filed)):
        issues.append("Missing or invalid SEC dates")
    else:
        days = (end - start).days

        if not 330 <= days <= 380:
            issues.append("Not a complete annual period")

        if not start <= end <= filed <= date.today():
            issues.append("Inconsistent or future SEC dates")

        if (date.today() - end).days > 500:
            issues.append("Annual observation is stale")

    if observation.get("form") != "10-K":
        issues.append("Source is not a 10-K")

    accession = observation.get("accession")

    if not isinstance(accession, str) or not accession:
        issues.append("Missing SEC accession number")

    return issues


def validate_company(record):
    ticker = record.get("ticker")
    expected = EXPECTED[ticker]

    technical_issues = []
    accounting_questions = []

    if record.get("field") != expected["field"]:
        technical_issues.append("Unexpected financial field")

    if record.get("cik") != expected["cik"]:
        technical_issues.append("SEC CIK mismatch")

    observations = record.get("observations_by_tag")

    if not isinstance(observations, dict):
        observations = {}
        technical_issues.append("Invalid observations structure")

    expected_tags = expected["tags"]
    found_tags = set(observations)

    for tag in sorted(expected_tags - found_tags):
        technical_issues.append(
            f"Expected tag unavailable: {tag}"
        )

    for tag in sorted(found_tags - expected_tags):
        technical_issues.append(
            f"Unexpected tag requires review: {tag}"
        )

    periods = set()
    accessions = set()

    for tag, observation in observations.items():
        problems = validate_observation(observation)

        technical_issues.extend(
            f"{tag}: {problem}"
            for problem in problems
        )

        if isinstance(observation, dict):
            periods.add((
                observation.get("period_start"),
                observation.get("period_end"),
            ))
            accessions.add(observation.get("accession"))

    if len(periods) != 1:
        technical_issues.append(
            "Observations do not share one fiscal year"
        )

    if len(accessions) != 1:
        technical_issues.append(
            "Observations come from different SEC filings"
        )

    if record.get("same_annual_period") is not True:
        technical_issues.append(
            "Pilot did not confirm same annual period"
        )

    if record.get("fcff_approved") is not False:
        technical_issues.append(
            "Unexpected FCFF approval flag"
        )

    if ticker == "MSFT":
        accounting_questions = [
            "Verify whether depreciation and intangible "
            "amortization are separate, non-overlapping items.",
            "Reconcile the components with Microsoft's "
            "official cash-flow statement and notes.",
            "Do not automatically sum these amounts.",
        ]

    elif ticker in ("NVDA", "AMZN"):
        accounting_questions = [
            "Review the precise meaning of "
            "PaymentsToAcquireProductiveAssets.",
            "Compare with the company's official "
            "capital expenditures disclosure.",
            "Check whether leases, intangible assets "
            "or other investments require adjustment.",
            "Do not automatically accept as FCFF CapEx.",
        ]

    return {
        "ticker": ticker,
        "field": expected["field"],
        "tags_found": sorted(found_tags),
        "technical_checks_passed": (
            len(technical_issues) == 0
        ),
        "technical_issues": technical_issues,
        "accounting_questions": accounting_questions,
        "accounting_validated": False,
        "approved_for_fcff": False,
        "status": (
            "ACCOUNTING_REVIEW_REQUIRED"
            if not technical_issues
            else "TECHNICAL_REVIEW_REQUIRED"
        ),
    }


def main():
    if not INPUT.exists():
        raise SystemExit(
            "Missing pilot report: "
            "data/pilot_xbrl_recovery_report.json. "
            "Run pilot_xbrl_recovery.py first."
        )

    pilot = read_json(INPUT)

    records = pilot.get("results", [])

    if not isinstance(records, list):
        raise SystemExit("Invalid pilot results")

    by_ticker = {}

    for record in records:
        if not isinstance(record, dict):
            raise SystemExit("Invalid company record")

        ticker = record.get("ticker")

        if ticker in by_ticker:
            raise SystemExit(
                f"Duplicate company: {ticker}"
            )

        if ticker not in EXPECTED:
            raise SystemExit(
                f"Unexpected company: {ticker}"
            )

        by_ticker[ticker] = record

    if pilot.get("errors"):
        raise SystemExit(
            "The recovery pilot contains errors"
        )

    if set(by_ticker) != set(EXPECTED):
        raise SystemExit(
            "Pilot does not include all three companies"
        )

    if (
        pilot.get("fcff_calculated") != 0
        or pilot.get("fcff_approved") != 0
        or pilot.get("automatic_dcf_enabled") is not False
    ):
        raise SystemExit(
            "Pilot safety flags are inconsistent"
        )

    results = [
        validate_company(by_ticker[ticker])
        for ticker in EXPECTED
    ]

    technical_passes = sum(
        item["technical_checks_passed"]
        for item in results
    )

    report = {
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "companies_checked": len(results),
        "technical_checks_passed": technical_passes,
        "accounting_validated": 0,
        "fcff_approved": 0,
        "automatic_dcf_enabled": False,
        "results": results,
        "notes": [
            "Checks SEC metadata and period consistency.",
            "Does not independently verify SEC values.",
            "Accounting equivalence remains unverified.",
            "MSFT D&A components are not automatically added.",
            "NVDA and AMZN CapEx figures are not approved.",
            "No FCFF is calculated.",
            "No production financial data is modified.",
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

    print("VALTRIX XBRL RECOVERY VALIDATION")
    print(f"Companies checked: {len(results)}")
    print(f"Technical checks passed: {technical_passes}")
    print("Accounting validations approved: 0")
    print("FCFF approved: 0")

    for result in results:
        print(
            f"{result['ticker']}: "
            f"{result['status']}"
        )

    if technical_passes != len(EXPECTED):
        raise SystemExit(
            "One or more technical checks failed"
        )


if __name__ == "__main__":
    main()
