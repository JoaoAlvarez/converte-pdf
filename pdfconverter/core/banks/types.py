"""Estruturas de dados dos parsers de fatura (espelham lib/parsers/types.ts)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedTransaction:
    date: str                          # DD/MM
    description: str
    installment_current: int | None    # ex.: 2 em "02/04"
    installment_total: int | None      # ex.: 4 em "02/04"
    city: str | None
    amount_cents: int
    is_credit: bool                    # pagamentos têm "-"


@dataclass
class ParsedCardSection:
    holder_name: str
    last_four_digits: str
    transactions: list[ParsedTransaction] = field(default_factory=list)


@dataclass
class ParsedStatement:
    brand: str                         # ELO, VISA, etc.
    card_number: str                   # ex.: "6504 XXXX XXXX 3934"
    due_date: str                      # DD/MM/YYYY
    total_cents: int
    billing_month: str                 # YYYY-MM (derivado de due_date)
    sections: list[ParsedCardSection] = field(default_factory=list)


def parse_brl_to_cents(value: str) -> int:
    """'R$ 1.234,56' -> 123456 (centavos)."""
    import re
    cleaned = re.sub(r"[R$\s]", "", value).replace(".", "").replace(",", ".")
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return 0


def derive_billing_month(due_date: str) -> str:
    import re
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", due_date)
    if not m:
        return ""
    return f"{m.group(3)}-{m.group(2)}"
