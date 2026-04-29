from __future__ import annotations

import re


PRICE_RE = re.compile(
    r"(?P<currency>rs\.?|inr|usd|eur|\$|₹)?\s*(?P<price>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:/-)?\s*(?:per|/)?\s*(?P<unit>pcs?|piece|sheet|sqm|sqft|kg|nos?)?",
    re.IGNORECASE,
)


def parse_quotation_text(text: str) -> list[dict]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    items = []
    for line in lines[:120]:
        match = PRICE_RE.search(line)
        if not match:
            continue
        price = float(match.group("price").replace(",", ""))
        if price <= 0:
            continue
        items.append(
            {
                "item_name": _guess_item(line),
                "specification": line[:500],
                "quantity": _guess_quantity(line),
                "unit": (match.group("unit") or "").lower(),
                "unit_price": price,
                "currency": _normalize_currency(match.group("currency")),
                "lead_time": _guess_lead_time(line),
                "notes": "",
            }
        )
    return items[:30]


def _guess_item(line: str) -> str:
    before_price = PRICE_RE.split(line, maxsplit=1)[0].strip(" -:|")
    return before_price[:160] or "Quoted item"


def _guess_quantity(line: str) -> str:
    match = re.search(r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:pcs|pieces|sheets|sheet|kg|sqm|sqft|nos|no)\b", line, re.IGNORECASE)
    return match.group(0) if match else ""


def _guess_lead_time(line: str) -> str:
    match = re.search(r"\b\d+\s*(?:day|days|week|weeks)\b", line, re.IGNORECASE)
    return match.group(0) if match else ""


def _normalize_currency(value: str | None) -> str:
    if not value:
        return "INR"
    value = value.lower()
    if value in {"rs", "rs.", "inr", "₹"}:
        return "INR"
    if value == "$":
        return "USD"
    return value.upper()
