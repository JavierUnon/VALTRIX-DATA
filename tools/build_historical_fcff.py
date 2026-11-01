
#!/usr/bin/env python3
"""VALTRIX: auditoria conservadora de FCFF SEC (no conecta con Lovable).

Uso desde la raiz del repositorio:
    python tools/build_historical_fcff.py

Lee data/companies/*.json y data/dcf_inputs/*.json.
Escribe exclusivamente data/historical_fcff_report.json.
"""
import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANIES = ROOT / "data" / "companies"
DCF_INPUTS = ROOT / "data" / "dcf_inputs"
OUT = ROOT / "data" / "historical_fcff_report.json"

# Son equivalencias de nombres de campos, NO equivalencias contables entre XBRL.
ALIASES = {
    "ebit": ("operating_income", "operating_income_loss", "ebit"),
    "da": ("depreciation_and_amortization", "depreciation_amortization", "da"),
    "capex": ("capital_expenditures", "capex", "payments_to_acquire_property_plant_and_equipment"),
    "tax": ("income_tax_expense", "income_taxes", "income_tax_expense_benefit"),
    "pretax": ("income_before_taxes", "pretax_income", "income_before_income_taxes"),
}
CRITICAL = ("ebit", "da", "capex", "tax", "pretax")


def load(path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        return {"_read_error": str(exc)}


def as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def find_key(node, aliases, limit=0):
    """Encuentra campos existentes; rechaza valores ambiguos o duplicados."""
    if limit > 7 or not isinstance(node, dict):
        return []
    found = []
    for k, v in node.items():
        if k.lower() in aliases:
            found.append(v)
    for k, v in node.items():
        if k.lower() not in aliases and isinstance(v, dict):
            found.extend(find_key(v, aliases, limit + 1))
    return found


def clean_fact(fact):
    """Solo acepta cifras con metadatos suficientes para auditarlas."""
    if not isinstance(fact, dict):
        return None
    value = as_number(fact.get("value"))
    end = fact.get("period_end") or fact.get("end")
    start = fact.get("period_start") or fact.get("start")
    unit = fact.get("unit", "USD")
    if value is None or not isinstance(end, str) or not isinstance(start, str):
        return None
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (ValueError, TypeError):
        return None
    if unit != "USD" or not 330 <= days <= 380:
        return None
    return {
        "value_usd": value,
        "start": start,
        "end": end,
        "tag": fact.get("tag"),
        "form": fact.get("form"),
        "method": fact.get("method"),
    }


def candidates(company, metric):
    """Extrae hechos anuales explícitos, sin fabricar años desde un TTM."""
    matched = find_key(company, ALIASES[metric])
    result = []
    for obj in matched:
        if isinstance(obj, dict):
            raw = [obj]
            for key in ("annual", "annual_history", "history", "years"):
                x = obj.get(key)
                if isinstance(x, list):
                    raw.extend(x)
                elif isinstance(x, dict):
                    raw.extend(v for v in x.values() if isinstance(v, dict))
            for item in raw:
                fact = clean_fact(item)
                if fact:
                    result.append(fact)
        elif isinstance(obj, list):
            for item in obj:
                fact = clean_fact(item)
                if fact:
                    result.append(fact)

    # Si hay dos cifras distintas para el mismo periodo, se marca ambiguo.
    by_period = {}
    for fact in result:
        key = (fact["start"], fact["end"])
        if key in by_period and by_period[key]["value_usd"] != fact["value_usd"]:
            by_period[key] = None
        elif key not in by_period:
            by_period[key] = fact
    return {key: val for key, val in by_period.items() if val is not None}


def nwc_for_year(inputs, end):
    """El NWC proporcionado es simplificado y solo sirve como aproximacion."""
    current, previous = inputs.get("current"), inputs.get("previous")
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return None

    fields = ("accounts_receivable", "inventory", "accounts_payable")

    try:
        now = [current[k] for k in fields]
        past = [previous[k] for k in fields]

        if any(not isinstance(x, dict) for x in now + past):
            return None
        if any(as_number(x.get("value")) is None for x in now + past):
            return None

        curr_dates = {x.get("period_end") for x in now}
        prev_dates = {x.get("period_end") for x in past}

        if curr_dates != {end} or len(prev_dates) != 1:
            return None

        earlier = date.fromisoformat(next(iter(prev_dates)))

        if not 350 <= (date.fromisoformat(end) - earlier).days <= 380:
            return None

        c = [float(x["value"]) for x in now]
        p = [float(x["value"]) for x in past]

        delta = (c[0] + c[1] - c[2]) - (p[0] + p[1] - p[2])

        supplied = as_number(inputs.get("simplified_change_in_nwc"))

        if supplied is None or abs(delta - supplied) > 1:
            return None

        return delta

    except (KeyError, ValueError, TypeError):
        return None


def evaluate(path):
    company = load(path)
    ticker = path.stem.upper()
    inp = load(DCF_INPUTS / f"{ticker}.json")

    row = {
        "ticker": ticker,
        "status": "insuficiente",
        "years": [],
        "warnings": [],
    }

    if "_read_error" in company:
        row["warnings"].append("No se pudo leer el JSON de la empresa")
        return row

    if "_read_error" in inp:
        row["warnings"].append(
            "No existe un JSON de capital circulante legible"
        )
        inp = {}

    facts = {m: candidates(company, m) for m in CRITICAL}

    periods = sorted(
        set().union(*(set(x) for x in facts.values())),
        key=lambda x: x[1],
        reverse=True,
    )[:12]

    for start, end in periods:
        available = {
            m: facts[m].get((start, end))
            for m in CRITICAL
        }

        states = {
            m: ("reportado" if available[m] else "ausente")
            for m in CRITICAL
        }

        year = {
            "period_start": start,
            "period_end": end,
            "components": states,
            "fcff_preliminary_usd": None,
            "status": "insuficiente",
            "warnings": [],
        }

        missing = [
            m for m in CRITICAL
            if available[m] is None
        ]

        if missing:
            year["warnings"].append(
                "Faltan componentes anuales: " + ", ".join(missing)
            )
        else:
            pretax = available["pretax"]["value_usd"]
            tax = available["tax"]["value_usd"]
            ebit = available["ebit"]["value_usd"]
            da = available["da"]["value_usd"]
            capex = available["capex"]["value_usd"]

            nwc = nwc_for_year(inp, end)

            if pretax <= 0 or tax < 0 or not 0 <= tax / pretax <= 0.50:
                year["warnings"].append(
                    "Tasa fiscal efectiva no utilizable sin revision"
                )

            elif nwc is None:
                year["warnings"].append(
                    "Falta NWC comparable para ese ejercicio"
                )

            elif ebit < 0 or da < 0 or capex < 0:
                year["warnings"].append(
                    "Signos de partidas requieren revision"
                )

            else:
                rate = tax / pretax

                year["components"]["tax"] = "aproximado"
                year["components"]["nwc"] = "aproximado"

                year["tax_rate_proxy"] = round(rate, 6)
                year["delta_nwc_simplified_usd"] = round(nwc)

                year["fcff_preliminary_usd"] = round(
                    ebit * (1 - rate) + da - capex - nwc
                )

                year["status"] = "requiere_revision"

                year["warnings"].append(
                    "Impuestos EBIT aproximados con tasa efectiva "
                    "de beneficios antes de impuestos"
                )

                year["warnings"].append(
                    "NWC simplificado: clientes + inventarios - "
                    "proveedores; faltan otras partidas"
                )

        row["years"].append(year)

    if any(
        y["fcff_preliminary_usd"] is not None
        for y in row["years"]
    ):
        row["status"] = "requiere_revision"
    else:
        row["warnings"].append(
            "Sin FCFF anual calculable bajo los controles estrictos"
        )

    return row


def main():
    if not COMPANIES.is_dir():
        raise SystemExit(
            "ERROR: Ejecuta desde un repositorio con "
            "data/companies/*.json"
        )

    files = sorted(COMPANIES.glob("*.json"))

    if not files:
        raise SystemExit(
            "ERROR: No hay archivos en data/companies"
        )

    companies = [
        evaluate(path)
        for path in files
    ]

    counts = Counter(
        c["status"]
        for c in companies
    )

    report = {
        "method": "annual_strict_v1",
        "companies_analyzed": len(companies),
        "status_counts": dict(counts),
        "automatic_dcf_enabled": False,
        "note": (
            "Un resultado 'requiere_revision' NO es un FCFF aprobado. "
            "Esta version no reconstruye tres anos si el repositorio "
            "no los contiene."
        ),
        "companies": companies,
    }

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUT.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"Analizadas: {len(companies)} empresas"
    )

    print(
        "Estados:", dict(counts)
    )

    print(
        "Informe:", OUT.relative_to(ROOT)
    )

    print(
        "FCFF aprobados automaticamente: 0"
    )


if __name__ == "__main__":
    main()
