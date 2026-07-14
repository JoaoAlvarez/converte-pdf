"""Parser de fatura do Nubank (portado de lib/parsers/nubank.ts)."""
from __future__ import annotations

import re

from .types import (
    ParsedCardSection,
    ParsedStatement,
    ParsedTransaction,
    derive_billing_month,
    parse_brl_to_cents,
)

MONTH_MAP: dict[str, str] = {
    "JAN": "01", "FEV": "02", "MAR": "03", "ABR": "04",
    "MAI": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SET": "09", "OUT": "10", "NOV": "11", "DEZ": "12",
}

MONTHS_PATTERN = "JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ"

# Old format: "DD MMM" at start of line (e.g. "04 FEV")
DATE_RE = re.compile(rf"^(\d{{2}})\s+({MONTHS_PATTERN})\b")

# New format: "DD MMM •••• NNNN Description [- Parcela N/N] R$ X,XX"
NEW_TX_RE = re.compile(
    rf"^(\d{{2}})\s+({MONTHS_PATTERN})\s+(?:••••\s+(\d{{4}})\s+)?(.+?)\s+R\$\s*([\d.,]+)$"
)

# Old format: value at end "Description R$ X,XX" or "−R$ X,XX" on next line
OLD_VALUE_RE = re.compile(r"\s+[-−]?R\$\s*([\d.,]+)$")


def parse_nubank_statement(text: str) -> ParsedStatement:
    lines = text.split("\n")

    due_date = _extract_due_date(lines)
    total_cents = _extract_total(lines)
    billing_month = derive_billing_month(due_date)
    sections = _parse_transactions(lines)

    return ParsedStatement(
        brand="MASTERCARD",
        card_number="",
        due_date=due_date,
        total_cents=total_cents,
        billing_month=billing_month,
        sections=sections,
    )


def _extract_due_date(lines: list[str]) -> str:
    for line in lines:
        match = re.search(
            r"Data de vencimento:\s*(\d{2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(\d{4})",
            line,
        )
        if match:
            day, month_name, year = match.group(1), match.group(2), match.group(3)
            return f"{day}/{MONTH_MAP[month_name]}/{year}"
    # Fallback: "FATURA DD MMM YYYY"
    for line in lines:
        match = re.search(
            r"FATURA\s+(\d{2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(\d{4})",
            line,
        )
        if match:
            day, month_name, year = match.group(1), match.group(2), match.group(3)
            return f"{day}/{MONTH_MAP[month_name]}/{year}"
    return ""


def _extract_total(lines: list[str]) -> int:
    for line in lines:
        match = re.search(r"Total a pagar\s+R\$\s*([\d.,]+)", line)
        if match:
            return parse_brl_to_cents(match.group(1))
    # Fallback: "no valor de\nR$ X.XXX,XX"
    for i in range(len(lines)):
        if "no valor de" in lines[i]:
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if nxt:
                match = re.search(r"R\$\s*([\d.,]+)", nxt)
                if match:
                    return parse_brl_to_cents(match.group(1))
    return 0


def _parse_transactions(lines: list[str]) -> list[ParsedCardSection]:
    card_groups: dict[str, dict] = {}
    in_transactions = False
    in_payments = False
    current_holder = ""

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Detect transaction section (both old and new format)
        if re.match(r"^TRANSAÇ", line):
            in_transactions = True
            in_payments = False
            i += 1
            continue

        # Detect payments section
        if line.startswith("Pagamentos") or line.startswith("Pagamento recebido"):
            if in_transactions:
                in_payments = True
            i += 1
            continue

        if not in_transactions or in_payments:
            i += 1
            continue

        # Skip page headers in new format
        if re.match(r"^JOÃO\s", line) or re.match(r"^FATURA\s", line):
            i += 1
            continue
        if re.match(r"^\d+\s+de\s+\d+$", line):  # "5 de 8"
            i += 1
            continue

        # Try new format: "DD MMM •••• NNNN Description R$ X,XX"
        new_match = NEW_TX_RE.match(line)
        if new_match:
            day = new_match.group(1)
            month_name = new_match.group(2)
            last_four_raw = new_match.group(3)
            middle = new_match.group(4)
            value_str = new_match.group(5)
            month = MONTH_MAP[month_name]
            date = f"{day}/{month}"
            last_four = last_four_raw or "_nubank"
            amount_cents = parse_brl_to_cents(value_str)

            if amount_cents == 0:
                i += 1
                continue

            # Skip credits (payments)
            if "−R$" in line or "-R$" in line:
                i += 1
                continue

            description, installment_current, installment_total = _parse_description(middle)

            key = last_four
            if key not in card_groups:
                card_groups[key] = {"holder_name": current_holder, "transactions": []}
            card_groups[key]["transactions"].append(
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

        # Try new format multi-line: international purchases
        # "16 FEV •••• 8958 Claude.Ai Subscription\nBRL 110.00 = USD 21.07\n...\nR$ 114,58"
        date_only_match = DATE_RE.match(line)
        if date_only_match and not new_match:
            day = date_only_match.group(1)
            month_name = date_only_match.group(2)
            month = MONTH_MAP[month_name]
            date = f"{day}/{month}"
            after_date = line[date_only_match.end():].strip()

            # Try to extract card number with •••• format
            last_four = "_nubank"
            description_part = after_date
            dots_match = re.match(r"^••••\s+(\d{4})\s+(.+)$", after_date)
            if dots_match:
                last_four = dots_match.group(1)
                description_part = dots_match.group(2)
            else:
                # Old format: non-letter chars followed by 4 digits
                card_match = re.match(r"^[^a-zA-Z\d]+(\d{4})\s+(.+)$", after_date)
                if card_match:
                    last_four = card_match.group(1)
                    description_part = card_match.group(2)

            # Check if value is on this line
            value_match = re.search(r"\s+[-−]?R\$\s*([\d.,]+)$", description_part)
            if value_match:
                amount_cents = parse_brl_to_cents(value_match.group(1))
                is_credit = bool(re.search(r"[-−]R\$", description_part[value_match.start():]))
                description_part = description_part[:value_match.start()].strip()

                if amount_cents == 0 or is_credit:
                    i += 1
                    continue

                description, installment_current, installment_total = _parse_description(description_part)

                if last_four not in card_groups:
                    card_groups[last_four] = {"holder_name": current_holder, "transactions": []}
                card_groups[last_four]["transactions"].append(
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

            # Multi-line: look ahead for R$ value (international purchases)
            for j in range(i + 1, min(i + 5, len(lines))):
                next_line = lines[j].strip()
                if DATE_RE.search(next_line):
                    break
                if next_line.startswith("Pagamentos"):
                    break
                val_match = re.match(r"^[-−]?R\$\s*([\d.,]+)$", next_line)
                if val_match:
                    amount_cents = parse_brl_to_cents(val_match.group(1))
                    is_credit = next_line.startswith("−") or next_line.startswith("-")
                    if amount_cents > 0 and not is_credit:
                        description, installment_current, installment_total = _parse_description(description_part)
                        if last_four not in card_groups:
                            card_groups[last_four] = {"holder_name": current_holder, "transactions": []}
                        card_groups[last_four]["transactions"].append(
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
                    i = j
                    break
            i += 1
            continue

        # Holder name line: "Joao R A T Barros R$ 4.364,99"
        if not DATE_RE.search(line):
            holder_match = re.match(r"^([A-Za-zÀ-ÿ\s]+?)\s+R\$\s*[\d.,]+$", line)
            if holder_match:
                current_holder = holder_match.group(1).strip()
            i += 1
            continue

        i += 1

    sections: list[ParsedCardSection] = []
    for last_four, group in card_groups.items():
        if len(group["transactions"]) > 0:
            sections.append(
                ParsedCardSection(
                    holder_name=group["holder_name"],
                    last_four_digits="" if last_four == "_nubank" else last_four,
                    transactions=group["transactions"],
                )
            )
    return sections


def _parse_description(desc: str):
    # "Via da Construcao - Parcela 3/3" or "Petz Digital - Parcela 2/3"
    parc_match = re.search(r"[-–]\s*Parcela\s+(\d+)/(\d+)$", desc, re.IGNORECASE)
    if parc_match:
        current = int(parc_match.group(1))
        total = int(parc_match.group(2))
        if 1 <= current <= total <= 48:
            description = desc[:parc_match.start()].strip()
            return description, current, total
    return desc, None, None
