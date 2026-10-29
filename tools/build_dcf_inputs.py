
"""VALTRIX - Preliminary DCF inputs from SEC filings.

Calculates a simplified operating working-capital change.
Does NOT calculate or certify unlevered FCFF.
"""

import json
import os
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

DATA = Path("data")
OUT = DATA / "dcf_inputs"
FIELDS = [
    "accounts_receivable",
    "inventory",
    "accounts_payable",
]


def get_company_facts(cik, user_agent):
    url = (
        "https://data.sec.gov/api/xbrl/companyfacts/"
        f"CIK{cik}.json"
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
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


def previous_balance(facts, current):
    if not isinstance(current, dict):
        return None

    tag = current.get("tag")
    end = current.get("period_end")
    if not tag or not end:
        return None

    current_date = date.fromisoformat(end)
    matches = []

    rows = (
        facts.get("us-gaap", {})
        .get(tag, {})
        .get("units", {})
        .get("USD", [])
    )

    for row in rows:
        if row.get("form") not in ("10-K", "10-Q"):
            continue
        if row.get("start"):
            continue
        if not row.get("end") or not row.get("filed"):
            continue

        try:
            previous_date = date.fromisoformat(row["end"])
            age = (current_date - previous_date).days
        except ValueError:
            continue

        if 350 <= age <= 380:
            matches.append(row)

    if not matches:
        return None

    selected = max(
        matches,
        key=lambda r: (r["end"], r["filed"]),
    )

    return {
        "value": selected["val"],
        "period_end": selected["end"],
        "filed": selected["filed"],
        "tag": tag,
        "unit": "USD",
    }


def process_company(record, facts):
    financials = record.get("financials", {})
    current = {}
    previous = {}
    issues = []

    for field in FIELDS:
        item = financials.get(field)
        current[field] = item

        if not isinstance(item, dict):
            previous[field] = None
            issues.append(f"Missing current {field}")
            continue

        previous[field] = previous_balance(facts, item)

        if previous[field] is None:
            issues.append(f"Missing previous-year {field}")

    periods = [
        current[field]["period_end"]
        for field in FIELDS
        if isinstance(current[field], dict)
    ]

    if len(periods) != 3 or len(set(periods)) != 1:
        issues.append("Current balance dates do not match")

    old_periods = [
        previous[field]["period_end"]
        for field in FIELDS
        if isinstance(previous[field], dict)
    ]

    if len(old_periods) != 3 or len(set(old_periods)) != 1:
        issues.append("Previous balance dates do not match")

    change = None

    if not issues:
        def nwc(values):
            return (
                values["accounts_receivable"]["value"]
                + values["inventory"]["value"]
                - values["accounts_payable"]["value"]
            )

        change = nwc(current) - nwc(previous)

    return {
        "ticker": record["ticker"],
        "company_name": record.get("company_name"),
        "current": current,
        "previous": previous,
        "simplified_change_in_nwc": change,
        "currency": "USD",
        "status": (
            "PRELIMINARY" if change is not None
            else "REVIEW_REQUIRED"
        ),
        "issues": issues,
        "warnings": [
            "Simplified NWC uses receivables + inventory - payables.",
            "Other operating assets and liabilities are excluded.",
            "Not a verified FCFF calculation.",
            "Not automatically suitable for the DCF engine.",
        ],
    }


def main():
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if "@" not in user_agent:
        raise SystemExit("Missing SEC_USER_AGENT secret.")

    index = json.loads(
        (DATA / "index.json").read_text(encoding="utf-8")
    )

    OUT.mkdir(parents=True, exist_ok=True)

    completed = 0
    preliminary = 0
    failures = []

    for number, company in enumerate(index, 1):
        ticker = company["ticker"]

        try:
            record = json.loads(
                (DATA / "companies" / f"{ticker}.json")
                .read_text(encoding="utf-8")
            )

            facts = get_company_facts(
                record["cik"], user_agent
            )["facts"]

            result = process_company(record, facts)

            (OUT / f"{ticker}.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            completed += 1

            if result["status"] == "PRELIMINARY":
                preliminary += 1

            print(
                f"[{number}/{len(index)}] "
                f"{ticker}: {result['status']}"
            )

        except Exception as error:
            failures.append({
                "ticker": ticker,
                "error": str(error),
            })
            print(f"ERROR {ticker}: {error}")

        time.sleep(0.35)

    report = {
        "total": len(index),
        "processed": completed,
        "preliminary_nwc_available": preliminary,
        "review_required_or_incomplete": completed - preliminary,
        "failures": failures,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "fcff_validated": 0,
    }

    (DATA / "dcf_inputs_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(
        f"Finished: {completed}/{len(index)}; "
        f"preliminary NWC: {preliminary}"
    )

    if failures:
        raise SystemExit("Some companies failed; see report.")


if __name__ == "__main__":
    main()
