"""Parser de fatura do PicPay (portado de lib/parsers/picpay.ts)."""
from __future__ import annotations

import re

from .types import (
    ParsedCardSection,
    ParsedStatement,
    ParsedTransaction,
    derive_billing_month,
    parse_brl_to_cents,
)

DATE_LINE_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2})$")
CARD_SECTION_RE = re.compile(r"Picpay Card final\s+(\d{4})")
SKIP_PATTERNS = [re.compile(r"^PAGAMENTO DE FATURA")]
PARC_RE = re.compile(r"PARC(\d{2})/(\d{2})$")


def parse_picpay_statement(text: str) -> ParsedStatement:
    lines = text.split("\n")

    brand = _extract_brand(lines)
    due_date = _extract_due_date(lines)
    total_cents = _extract_total(lines)
    billing_month = derive_billing_month(due_date)
    card_number = _extract_card_number(lines)
    closing_date = _extract_closing_date(lines)
    sections = _parse_transaction_sections(lines)

    saldo_anterior = _extract_saldo_anterior(lines)
    if saldo_anterior > 0 and sections:
        sections[0].transactions.append(
            ParsedTransaction(
                date=closing_date or due_date[:5],
                description="SALDO FATURA ANTERIOR",
                installment_current=None,
                installment_total=None,
                city=None,
                amount_cents=saldo_anterior,
                is_credit=False,
            )
        )

    return ParsedStatement(
        brand=brand,
        card_number=card_number,
        due_date=due_date,
        total_cents=total_cents,
        billing_month=billing_month,
        sections=sections,
    )


def _extract_brand(lines: list[str]) -> str:
    for line in lines:
        if "Mastercard" in line:
            return "MASTERCARD"
        if "Visa" in line:
            return "VISA"
        if "Elo" in line:
            return "ELO"
    return "MASTERCARD"


def _extract_due_date(lines: list[str]) -> str:
    for i, line in enumerate(lines):
        if line.strip() == "Vencimento":
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", nxt):
                return nxt
    for line in lines:
        m = re.search(r"(\d{2})-(\d{2})-(\d{4})\s*\|.*Vencimento", line)
        if m:
            return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return ""


def _extract_total(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.strip() == "Total da sua fatura":
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if nxt:
                return parse_brl_to_cents(nxt)
        m = re.search(r"Total da (?:sua )?fatura\s+R\$\s*([\d.,]+)", line)
        if m:
            return parse_brl_to_cents(m.group(1))
    return 0


def _extract_card_number(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"Picpay Card final\s+(\d{4})", line)
        if m:
            return f"XXXX XXXX XXXX {m.group(1)}"
    return ""


def _extract_closing_date(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"\|\s*(\d{2})-(\d{2})-\d{4}\s*Vencimento", line)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return ""


def _extract_saldo_anterior(lines: list[str]) -> int:
    fatura_anterior = 0
    pagamento_recebido = 0
    for line in lines:
        t = line.strip()
        m = re.match(r"^Fatura anterior\s+([\d.,]+)", t)
        if m:
            fatura_anterior = parse_brl_to_cents(m.group(1))
        m = re.match(r"^Pagamento recebido\s+-?([\d.,]+)", t)
        if m:
            pagamento_recebido = parse_brl_to_cents(m.group(1))
    saldo = fatura_anterior - pagamento_recebido
    return saldo if saldo > 0 else 0


def _parse_description(middle: str):
    m = PARC_RE.search(middle)
    if m:
        current, total = int(m.group(1)), int(m.group(2))
        if 1 <= current <= total <= 48:
            return middle[: m.start()].strip(), current, total
    return middle, None, None


def _parse_transaction_sections(lines: list[str]) -> list[ParsedCardSection]:
    sections: list[ParsedCardSection] = []
    current_section: ParsedCardSection | None = None
    in_transactions = False
    in_tarifas = False
    pending: list[ParsedTransaction] = []

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        sm = CARD_SECTION_RE.search(line)
        if sm:
            holder = ""
            for j in range(i - 1, max(-1, i - 4), -1):
                prev = lines[j].strip()
                if (prev and not prev.startswith(("Picpay", "Subtotal", "Data", "Transaç"))
                        and not re.match(r"^\d{2}/\d{2}\s", prev)):
                    holder = prev
                    break
            current_section = ParsedCardSection(holder, sm.group(1), [])
            sections.append(current_section)
            if pending:
                current_section.transactions.extend(pending)
                pending.clear()
            in_transactions = in_tarifas = False
            continue

        if line == "Tarifas":
            in_tarifas, in_transactions = True, False
            continue
        if line.startswith("Transações Nacionais") or line.startswith("Transações Internacionais"):
            in_transactions, in_tarifas = True, False
            continue
        if line.startswith("Data ") and ("Operação" in line or "Estabelecimento" in line):
            continue
        if line.startswith("Total geral"):
            break
        if line.startswith("Subtotal"):
            continue
        if not in_transactions and not in_tarifas:
            continue

        m = DATE_LINE_RE.match(line)
        if not m:
            continue
        date, middle, value_str = m.group(1), m.group(2), m.group(3)
        if any(p.search(middle) for p in SKIP_PATTERNS):
            continue
        desc, cur, tot = _parse_description(middle)
        tx = ParsedTransaction(date, desc, cur, tot, None, parse_brl_to_cents(value_str), False)
        (current_section.transactions if current_section else pending).append(tx)

    return [s for s in sections if s.transactions]
