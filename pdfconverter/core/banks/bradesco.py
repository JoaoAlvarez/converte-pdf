"""Parser de fatura do Bradesco (portado de lib/parsers/bradesco.ts)."""
from __future__ import annotations

import re

from .types import (
    ParsedCardSection,
    ParsedStatement,
    ParsedTransaction,
    derive_billing_month,
    parse_brl_to_cents,
)

# Regex patterns
DATE_LINE_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")
VALUE_RE = re.compile(r"^([\d.]+,\d{2})(\s*-)?$")
VALUE_END_RE = re.compile(r"([\d.]+,\d{2})(\s*-)?$")
CARD_SECTION_RE = re.compile(r"Cart(?:ã|a)o\s+(\d{4}\s+XXXX\s+XXXX\s+(\d{4}))")
TOTAL_LINE_RE = re.compile(r"^Total\s+(?:para|da\s+fatura)")
INSTALLMENT_STANDALONE_RE = re.compile(r"^(\d{2})/(\d{2})$")

# Split pattern for holder name extraction ("everything before Cartão")
CARD_SPLIT_RE = re.compile(r"Cart(?:ã|a)o")

# Full transaction line pattern used by parse_transaction_line
TRANSACTION_LINE_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2})(\s*-)?$")

# Installment pattern inside the middle segment
INSTALLMENT_RE = re.compile(r"\b(\d{2})/(\d{2})\b")


def parse_bradesco_statement(text: str) -> ParsedStatement:
    """Parse a Bradesco credit card statement from extracted PDF text."""
    lines = text.split("\n")

    # Extract brand from header (e.g. "ELO MAIS EXCLUSIVE" or "VISA SIGNATURE")
    brand = _extract_brand(lines)

    # Extract due date
    due_date = _extract_due_date(lines)

    # Extract total
    total_cents = _extract_total(lines)

    # Extract card number from "Número do Cartão XXXX XXXX XXXX DDDD"
    card_number = _extract_card_number(lines)

    # Derive billing month from due date (DD/MM/YYYY -> YYYY-MM)
    billing_month = derive_billing_month(due_date)

    # Parse transaction sections
    sections = _parse_transaction_sections(lines, billing_month)

    return ParsedStatement(
        brand=brand,
        card_number=card_number,
        due_date=due_date,
        total_cents=total_cents,
        billing_month=billing_month,
        sections=sections,
    )


def _extract_brand(lines: list[str]) -> str:
    brands = ["ELO", "VISA", "MASTERCARD", "AMEX", "HIPERCARD"]
    for line in lines:
        upper = line.strip().upper()
        for b in brands:
            if upper.startswith(b) and (
                "EXCLUSIVE" in upper
                or "SIGNATURE" in upper
                or "PLATINUM" in upper
                or "GOLD" in upper
                or "BLACK" in upper
                or "INFINITE" in upper
                or "INTERNACIONAL" in upper
                or upper == b
            ):
                return b
    return "OUTRO"


def _extract_due_date(lines: list[str]) -> str:
    for i in range(len(lines)):
        if lines[i].strip() == "Vencimento":
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else None
            if nxt and re.fullmatch(r"\d{2}/\d{2}/\d{4}", nxt):
                return nxt
    return ""


def _extract_total(lines: list[str]) -> int:
    for i in range(len(lines)):
        if lines[i].strip() == "Total da fatura":
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else None
            if nxt:
                return parse_brl_to_cents(nxt)
    return 0


def _extract_card_number(lines: list[str]) -> str:
    for line in lines:
        match = re.search(r"Número do Cartão\s+(\d{4}\s+XXXX\s+XXXX\s+\d{4})", line)
        if match:
            return match.group(1)
    return ""


def _parse_transaction_sections(
    lines: list[str], billing_month: str
) -> list[ParsedCardSection]:
    # Find the start of "Lançamentos" section
    start_idx = -1
    for i in range(len(lines)):
        if lines[i].strip() == "Lançamentos":
            start_idx = i
            break
    if start_idx == -1:
        return []

    # Skip the header line(s) after "Lançamentos"
    i = start_idx + 1
    # Skip "Data Histórico de Lançamentos Cidade US$ Cotação" and "do Dólar R$"
    while (
        i < len(lines)
        and not DATE_LINE_RE.search(lines[i].strip())
        and not CARD_SECTION_RE.search(lines[i])
    ):
        i += 1

    sections: list[ParsedCardSection] = []
    current_section: ParsedCardSection | None = None
    buffer: list[str] = []  # For multi-line entries
    last_transaction: ParsedTransaction | None = None

    def flush_buffer() -> None:
        nonlocal buffer, last_transaction
        if len(buffer) == 0:
            return
        combined = " ".join(buffer)
        tx = _parse_transaction_line(combined, billing_month)
        if tx and not tx.is_credit:
            last_transaction = tx
            if current_section is not None:
                current_section.transactions.append(tx)
        buffer = []

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Stop at end markers
        if line.startswith("Total da fatura em real"):
            break
        if line.startswith("Mensagem Importante"):
            break
        if line.startswith("Total parcelados"):
            break

        # Total lines - skip
        if TOTAL_LINE_RE.search(line):
            flush_buffer()
            last_transaction = None
            # Skip possible continuation of "Total para NAME\nNAME VALUE"
            i += 1
            continue

        # Card section header
        section_match = CARD_SECTION_RE.search(line)
        if section_match:
            flush_buffer()
            last_transaction = None
            # Extract holder name: everything before "Cartão"
            holder_name = CARD_SPLIT_RE.split(line)[0].strip()
            current_section = ParsedCardSection(
                holder_name=holder_name,
                last_four_digits=section_match.group(2),
                transactions=[],
            )
            sections.append(current_section)
            i += 1
            continue

        # If no section yet, create a default one
        if current_section is None:
            current_section = ParsedCardSection(
                holder_name="", last_four_digits="", transactions=[]
            )
            sections.append(current_section)

        # Check for standalone installment (e.g. "12/12" on its own line after a transaction)
        installment_match = INSTALLMENT_STANDALONE_RE.match(line)
        if installment_match:
            n1 = int(installment_match.group(1))
            n2 = int(installment_match.group(2))
            if n1 >= 1 and n1 <= n2 and n2 <= 48:
                # Only applies if buffer is empty (previous transaction was complete)
                if (
                    len(buffer) == 0
                    and last_transaction is not None
                    and last_transaction.installment_current is None
                ):
                    last_transaction.installment_current = n1
                    last_transaction.installment_total = n2
                    i += 1
                    continue

        # Line starts with a date?
        date_match = DATE_LINE_RE.match(line)
        if date_match:
            flush_buffer()

            rest = date_match.group(2)
            # Check if this line has a value at the end
            value_match = VALUE_END_RE.search(rest)
            if value_match:
                # Complete transaction on one line
                tx = _parse_transaction_line(line, billing_month)
                if tx:
                    if tx.is_credit:
                        last_transaction = None
                    else:
                        last_transaction = tx
                        current_section.transactions.append(tx)
            else:
                # Start of multi-line entry
                buffer = [line]
            i += 1
            continue

        # If we have a buffer, check if this line is a value
        if len(buffer) > 0:
            value_only = VALUE_RE.match(line)
            if value_only:
                # Append value to buffer and flush
                buffer.append(line)
                flush_buffer()
            else:
                # Continuation line (city name etc.)
                buffer.append(line)
            i += 1
            continue

        # Skip other lines (totals continuation, headers, footers, etc.)
        i += 1

    flush_buffer()
    return [s for s in sections if len(s.transactions) > 0]


def _parse_transaction_line(
    line: str, billing_month: str
) -> ParsedTransaction | None:
    # Expected: "DD/MM DESCRIPTION [NN/NN] [CITY] VALUE[-]"
    date_match = TRANSACTION_LINE_RE.match(line)
    if not date_match:
        return None

    date = date_match.group(1)
    middle = date_match.group(2)
    value_str = date_match.group(3)
    credit = date_match.group(4)
    amount_cents = parse_brl_to_cents(value_str)
    is_credit = bool(credit)

    # Extract installment from middle: look for NN/NN pattern
    description, installment_current, installment_total, city = _parse_middle(middle)

    return ParsedTransaction(
        date=date,
        description=description,
        installment_current=installment_current,
        installment_total=installment_total,
        city=city,
        amount_cents=amount_cents,
        is_credit=is_credit,
    )


def _parse_middle(middle: str):
    # Find installment pattern NN/NN in the middle
    # Must be: first number <= second number, both <= 48, and not at the very start
    best_match = None

    for m in INSTALLMENT_RE.finditer(middle):
        current = int(m.group(1))
        total = int(m.group(2))
        if current >= 1 and current <= total and total <= 48:
            best_match = {
                "index": m.start(),
                "current": current,
                "total": total,
                "full_match": m.group(0),
            }

    if best_match:
        before_installment = middle[: best_match["index"]].strip()
        after_installment = middle[
            best_match["index"] + len(best_match["full_match"]) :
        ].strip()
        return (
            before_installment,
            best_match["current"],
            best_match["total"],
            after_installment or None,
        )

    # No installment found - keep full text as description
    # (separating description from city is unreliable since both are uppercase)
    return middle, None, None, None
