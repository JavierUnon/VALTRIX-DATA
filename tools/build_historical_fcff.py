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
    return {"value_usd": value, "start": start, "end": end,
            "tag": fact.get("tag"), "form": fact.get("form"),
            "method": fact.get("method")}


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
