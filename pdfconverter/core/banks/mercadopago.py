"""Parser de fatura do Mercado Pago (portado de lib/parsers/mercadopago.ts)."""
from __future__ import annotations

import re

from .types import (
    ParsedCardSection,
    ParsedStatement,
    ParsedTransaction,
    derive_billing_month,
    parse_brl_to_cents,
)

CARD_SECTION_RE = re.compile(r"Cartão\s+\w+\s+\[\*{12}(\d{4})\]")

# Inline format: "DD/MM Description R$ X,XX" or "DD/MM Description Parcela N de N R$ X,XX"
INLINE_TX_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+R\$\s*([\d.,]+)$")

# Multi-line format: just a date "DD/MM"
DATE_ONLY_RE = re.compile(r"^\d{2}/\d{2}$")
VALUE_RE = re.compile(r"^R\$\s*([\d.,]+)$")

PARCELA_RE = re.compile(r"^Parcela\s+(\d+)\s+de\s+(\d+)$")
PARCELA_DESC_RE = re.compile(r"\s+Parcela\s+(\d+)\s+de\s+(\d+)$", re.IGNORECASE)

SKIP_DESCRIPTIONS = [
    "Pagamento da fatura",
    "Débito para pagar a fatura",
    "Juros de mora",
    "Juros do rotativo",
    "IOF do rotativo",
    "Multa por atraso",
]


def parse_mercadopago_statement(text: str) -> ParsedStatement:
    """Parse a Mercado Pago credit card statement from extracted PDF text.

    Handles inline format: "DD/MM Description [Parcela N de N] R$ X,XX"
    and multi-line format (from getDocumentProxy with password).
    """
    # Replica text.split('\n').map(l=>l.trim()).filter(l=>l.length>0)
    lines = [s for s in (l.strip() for l in text.split("\n")) if len(s) > 0]

    due_date = _extract_due_date(lines)
    total_cents = _extract_total(lines)
    billing_month = derive_billing_month(due_date)
    brand = _extract_brand(lines)
    sections = _parse_transaction_sections(lines)

    return ParsedStatement(
        brand=brand,
        card_number="",
        due_date=due_date,
        total_cents=total_cents,
        billing_month=billing_month,
        sections=sections,
    )


def _extract_brand(lines: list[str]) -> str:
    for line in lines:
        if "Cartão Visa" in line:
            return "VISA"
        if "Cartão Mastercard" in line:
            return "MASTERCARD"
        if "Cartão Elo" in line:
            return "ELO"
    return "VISA"


def _extract_due_date(lines: list[str]) -> str:
    for i, line in enumerate(lines):
        if line == "Vence em":
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and re.fullmatch(r"\d{2}/\d{2}/\d{4}", nxt):
                return nxt
        m = re.search(r"Vencimento:\s*(\d{2}/\d{2}/\d{4})", line)
        if m:
            return m.group(1)
    return ""


def _extract_total(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line == "Total a pagar":
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt:
                m = re.search(r"R\$\s*([\d.,]+)", nxt)
                if m:
                    return parse_brl_to_cents(m.group(1))
    return 0


def _should_skip(desc: str) -> bool:
    return any(desc.startswith(s) for s in SKIP_DESCRIPTIONS)


def _parse_transaction_sections(lines: list[str]) -> list[ParsedCardSection]:
    card_groups: dict[str, list[ParsedTransaction]] = {}
    current_last_four = ""
    in_movimentacoes = False
    in_card_section = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect card section
        card_match = CARD_SECTION_RE.search(line)
        if card_match:
            current_last_four = card_match.group(1)
            in_card_section = True
            in_movimentacoes = False
            if current_last_four not in card_groups:
                card_groups[current_last_four] = []
            i += 1
            continue

        # Detect movimentações section
        if line == "Movimentações na fatura":
            in_movimentacoes = True
            in_card_section = False
            i += 1
            continue

        # Skip headers
        if re.match(r"^Data\s+Movimentações", line):
            i += 1
            continue
        if line == "Data" or line == "Movimentações" or line.startswith("Valor em R$"):
            i += 1
            continue
        if line.startswith("Detalhes de consumo"):
            i += 1
            continue
        if line.startswith("Parcele a fatura"):
            in_card_section = False
            i += 1
            continue

        # Skip Total lines
        if re.match(r"^Total\b", line):
            i += 1
            continue

        if not in_card_section and not in_movimentacoes:
            i += 1
            continue

        # Try INLINE format first: "DD/MM Description [Parcela N de N] R$ X,XX"
        inline_match = INLINE_TX_RE.match(line)
        if inline_match:
            date = inline_match.group(1)
            middle = inline_match.group(2)
            val_str = inline_match.group(3)
            amount_cents = parse_brl_to_cents(val_str)

            if _should_skip(middle) or in_movimentacoes:
                i += 1
                continue

            if amount_cents > 0 and current_last_four:
                description, installment_current, installment_total = _parse_description(middle)
                card_groups[current_last_four].append(
                    ParsedTransaction(
                        date=date,
                        description=description,
                        installment_current=installment_current,
                        installment_total=installment_total,
                        city=None,
                        amount_cents=amount_cents,
                        is_credit=False,
                    )
                )
            i += 1
            continue

        # Try MULTI-LINE format: date on its own line
        if not DATE_ONLY_RE.match(line):
            i += 1
            continue

        date = line
        j = i + 1

        # Skip stray headers
        while j < len(lines) and (
            lines[j] == "Data"
            or lines[j] == "Movimentações"
            or lines[j].startswith("Valor em R$")
        ):
            j += 1
        if j >= len(lines):
            i += 1
            continue

        description = lines[j]

        if _should_skip(description) or in_movimentacoes:
            i = j
            i += 1
            continue

        # Look for optional "Parcela N de N" and then "R$ X,XX"
        k = j + 1
        installment_current: int | None = None
        installment_total: int | None = None

        if k < len(lines):
            parc_match = PARCELA_RE.match(lines[k])
            if parc_match:
                installment_current = int(parc_match.group(1))
                installment_total = int(parc_match.group(2))
                k += 1

        if k < len(lines):
            value_match = VALUE_RE.match(lines[k])
            if value_match:
                amount_cents = parse_brl_to_cents(value_match.group(1))
                if amount_cents > 0 and current_last_four:
                    card_groups[current_last_four].append(
                        ParsedTransaction(
                            date=date,
                            description=description,
                            installment_current=installment_current,
                            installment_total=installment_total,
                            city=None,
                            amount_cents=amount_cents,
                            is_credit=False,
                        )
                    )
                i = k
                i += 1
                continue

        i += 1

    sections: list[ParsedCardSection] = []
    for last_four, transactions in card_groups.items():
        if len(transactions) > 0:
            sections.append(
                ParsedCardSection(
                    holder_name="",
                    last_four_digits=last_four,
                    transactions=transactions,
                )
            )
    return sections


def _parse_description(desc: str):
    parc_match = PARCELA_DESC_RE.search(desc)
    if parc_match:
        current = int(parc_match.group(1))
        total = int(parc_match.group(2))
        if 1 <= current <= total <= 48:
            return desc[: parc_match.start()].strip(), current, total
    return desc, None, None
