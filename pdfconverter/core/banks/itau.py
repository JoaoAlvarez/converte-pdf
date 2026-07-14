"""Parser de fatura do Itaú (portado de lib/parsers/itau.ts).

As faturas do Itaú usam layout em duas colunas. A extração de texto funde as
colunas em linhas únicas, então cada linha pode conter conteúdo de ambas.
Estratégia:
 1. Encontrar TODAS as transações em cada linha buscando padrões DD/MM + VALOR
 2. A primeira transação numa linha iniciada por DD/MM é a coluna esquerda (compras)
 3. Transações adicionais são coluna direita (produtos/serviços)
 4. Usar heurísticas para separar compras de serviços em linhas muito fundidas
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .types import (
    ParsedCardSection,
    ParsedStatement,
    ParsedTransaction,
    derive_billing_month,
    parse_brl_to_cents,
)


def parse_itau_statement(text: str) -> ParsedStatement:
    lines = text.split("\n")

    due_date = _extract_due_date(lines)
    total_cents = _extract_total(lines)
    card_number = _extract_card_number(lines)
    brand = _detect_brand(card_number)
    billing_month = derive_billing_month(due_date)

    sections = _parse_sections(lines)

    return ParsedStatement(
        brand=brand,
        card_number=card_number,
        due_date=due_date,
        total_cents=total_cents,
        billing_month=billing_month,
        sections=sections,
    )


# ------------------------------------------------------------------ #
#  Header extraction                                                  #
# ------------------------------------------------------------------ #


def _extract_due_date(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"Vencimento:\s*(\d{2}/\d{2}/\d{4})", line)
        if m:
            return m.group(1)
    for line in lines:
        m = re.search(r"vencimento em:\s*(\d{2}/\d{2}/\d{4})", line)
        if m:
            return m.group(1)
    return ""


def _extract_total(lines: list[str]) -> int:
    for line in lines:
        m = re.search(r"Total desta fatura\s+([\d.,]+)", line)
        if m:
            return parse_brl_to_cents(m.group(1))
    for line in lines:
        m = re.search(r"total da sua fatura.*?R\$\s*([\d.,]+)", line)
        if m:
            return parse_brl_to_cents(m.group(1))
    return 0


def _extract_card_number(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"Cart[ãa]o\s+(\d{4}[.\s]XXXX[.\s]XXXX[.\s]\d{4})", line)
        if m:
            return re.sub(r"\.", " ", m.group(1))
    # Card number may be on a standalone line (different text extraction order)
    for line in lines:
        m = re.match(r"^(\d{4}[.\s]XXXX[.\s]XXXX[.\s]\d{4})$", line.strip())
        if m:
            return re.sub(r"\.", " ", m.group(1))
    return ""


def _detect_brand(card_number: str) -> str:
    first = card_number[0:1]
    if first == "4":
        return "VISA"
    if first == "5":
        return "MASTERCARD"
    if first == "3":
        return "AMEX"
    if first == "6":
        return "ELO"
    return "OUTRO"


# ------------------------------------------------------------------ #
#  Utilities                                                          #
# ------------------------------------------------------------------ #


@dataclass
class RawTx:
    date: str
    description: str
    value: str
    is_credit: bool


VALUE_RE = re.compile(r"([\d.]+,\d{2})")


def _find_transactions_in_text(text: str) -> list[RawTx]:
    """Find all transactions in a text fragment by scanning for DD/MM patterns
    followed by description text and a BRL value (NNN,NN).

    Handles two-column merged lines by finding successive DD/MM + VALUE pairs.
    Credit transactions are detected by a trailing " -" before the value.
    """
    results: list[RawTx] = []
    remaining = text

    while len(remaining) > 0:
        # Find next DD/MM followed by space
        date_match = re.search(r"(\d{2})/(\d{2})\s+", remaining)
        if not date_match:
            break

        date_pos = date_match.start()
        date_str = f"{date_match.group(1)}/{date_match.group(2)}"
        after_date = remaining[date_pos + len(date_match.group(0)):]

        # Skip if immediately followed by a value (this DD/MM is an installment, not a date)
        if re.match(r"^[\d.]+,\d{2}", after_date):
            remaining = remaining[date_pos + len(date_match.group(0)):]
            continue

        # Find the first BRL value pattern in the text after the date
        value_match = VALUE_RE.search(after_date)
        if not value_match:
            break

        value_str = value_match.group(1)
        desc_text = after_date[0:value_match.start()].strip()

        # Detect credit: description ends with " -" or "-"
        is_credit = bool(re.search(r"\s*-\s*$", desc_text))
        clean_desc = re.sub(r"\s*-\s*$", "", desc_text).strip() if is_credit else desc_text

        # Skip subtotals, section headers, and empty descriptions
        if (
            clean_desc
            and not re.match(r"^Total", clean_desc, re.IGNORECASE)
            and not re.match(r"^Lan[çc]amentos", clean_desc, re.IGNORECASE)
        ):
            results.append(
                RawTx(date=date_str, description=clean_desc, value=value_str, is_credit=is_credit)
            )

        # Advance past this transaction
        advance = date_pos + len(date_match.group(0)) + value_match.start() + len(value_str)
        remaining = remaining[advance:]

    return results


def _build_transaction(raw: RawTx) -> ParsedTransaction:
    description = raw.description
    installment_current: int | None = None
    installment_total: int | None = None

    # Extract installment from end of description: "DESC NN/NN" or "DESCNN/NN"
    inst_match = re.search(r"\s*(\d{2})/(\d{2})\s*$", description)
    if inst_match:
        n1 = int(inst_match.group(1))
        n2 = int(inst_match.group(2))
        if n1 >= 1 and n1 <= n2 and n2 <= 48:
            installment_current = n1
            installment_total = n2
            description = description[0:inst_match.start()].strip()

    return ParsedTransaction(
        date=raw.date,
        description=description,
        installment_current=installment_current,
        installment_total=installment_total,
        city=None,
        amount_cents=parse_brl_to_cents(raw.value),
        is_credit=raw.is_credit,
    )


# ------------------------------------------------------------------ #
#  Section parsing                                                    #
# ------------------------------------------------------------------ #

CATEGORY_LINE_RE = re.compile(r"^([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]{3,})\s*\.")


def _parse_sections(lines: list[str]) -> list[ParsedCardSection]:
    # Find start of lançamentos
    start_idx = -1
    for i in range(len(lines)):
        if re.search(r"Lan[çc]amentos:?\s*compras e saques", lines[i], re.IGNORECASE):
            start_idx = i
            break
    if start_idx == -1:
        return []

    # Find "Continua..." page break
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        if re.match(r"^Continua\.{3}$", lines[i].strip(), re.IGNORECASE):
            end_idx = i
            break

    compras_sections: list[ParsedCardSection] = []
    servicos_section = ParsedCardSection(
        holder_name="Produtos e Serviços",
        last_four_digits="",
        transactions=[],
    )

    current_compras_section: ParsedCardSection | None = None
    last_left_tx: ParsedTransaction | None = None

    for i in range(start_idx, end_idx):
        line = lines[i].strip()
        if not line:
            continue

        # ── 1. Lines starting with DD/MM: always process as transaction first ──
        if re.match(r"^\d{2}/\d{2}\s+", line):
            all_txs = _find_transactions_in_text(line)
            if len(all_txs) == 0:
                continue

            # First transaction → left column (compras e saques)
            left_tx = _build_transaction(all_txs[0])
            if current_compras_section:
                current_compras_section.transactions.append(left_tx)
                last_left_tx = left_tx

            # Remaining transactions → right column or merged compras
            for j in range(1, len(all_txs)):
                tx = _build_transaction(all_txs[j])
                if current_compras_section and _looks_like_compra(tx.description, current_compras_section):
                    current_compras_section.transactions.append(tx)
                else:
                    servicos_section.transactions.append(tx)
            continue

        # ── 2. Skip header lines ──
        if re.match(r"^DATA\s+(ESTABELECIMENTO|PRODUTOS|DESCRI)", line, re.IGNORECASE):
            continue
        if re.match(r"^VALOR EM R\$", line, re.IGNORECASE):
            continue
        if re.match(r"^Lan[çc]amentos:?\s*(compras|produtos)", line, re.IGNORECASE):
            continue
        if re.match(r"^Principal\s+\(R\$", line, re.IGNORECASE):
            continue
        if re.match(r"^Titular\s+\d{4}", line, re.IGNORECASE):
            continue

        # ── 3. Card holder header: "NAME (final NNNN)" ──
        holder_match = re.match(r"^([A-Z][A-Z\s]+?)\s*\(final\s+(\d{4})\)", line)
        if holder_match:
            current_compras_section = ParsedCardSection(
                holder_name=holder_match.group(1).strip(),
                last_four_digits=holder_match.group(2),
                transactions=[],
            )
            compras_sections.append(current_compras_section)

            # Right-column content after the holder header
            rest = line[holder_match.end():].strip()
            if rest:
                right_txs = _find_transactions_in_text(rest)
                for raw in right_txs:
                    servicos_section.transactions.append(_build_transaction(raw))
            continue

        # ── 4. Category/city line: "VESTUÁRIO .RECIFE [merged right-column]" ──
        if CATEGORY_LINE_RE.match(line):
            cat_match = re.match(r"^[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]+\s*\.(\w*)", line)
            if cat_match and last_left_tx:
                city = cat_match.group(1).strip()
                if city:
                    last_left_tx.city = city

            # Check for right-column transactions merged after the category
            after_category = re.sub(r"^[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]+\s*\.\S*\s*", "", line).strip()
            if after_category:
                right_txs = _find_transactions_in_text(after_category)
                for raw in right_txs:
                    servicos_section.transactions.append(_build_transaction(raw))
            continue

        # ── 5. Subtotal lines: "Lançamentos no cartão (final 8829) 66,00 [right-col]" ──
        if re.match(r"^Lan[çc]amentos\s+(no cart|produtos|outros)", line, re.IGNORECASE):
            # Look for right-column transactions after the subtotal value
            after_subtotal = re.sub(r"^.*?\d+,\d{2}\s*", "", line).strip()
            if after_subtotal:
                right_txs = _find_transactions_in_text(after_subtotal)
                for raw in right_txs:
                    servicos_section.transactions.append(_build_transaction(raw))
            continue

        # ── 6. "Outros lançamentos" merged line ──
        if re.search(r"Outros\s+lan[çc]amentos", line, re.IGNORECASE):
            all_txs = _find_transactions_in_text(line)
            for j in range(len(all_txs)):
                tx = _build_transaction(all_txs[j])
                if j == 0 and current_compras_section:
                    # First transaction is likely the left-column compra
                    current_compras_section.transactions.append(tx)
                    last_left_tx = tx
                elif current_compras_section and _looks_like_compra(tx.description, current_compras_section):
                    current_compras_section.transactions.append(tx)
                else:
                    servicos_section.transactions.append(tx)
            continue

    result = [s for s in compras_sections if len(s.transactions) > 0]
    if len(servicos_section.transactions) > 0:
        result.append(servicos_section)

    # Fallback: some PDFs have transactions BEFORE the section header
    # (text extraction order varies between PDF versions)
    if len(result) == 0 and start_idx > 0:
        return _parse_sections_fallback(lines, start_idx)

    return result


def _parse_sections_fallback(
    lines: list[str],
    header_idx: int,
) -> list[ParsedCardSection]:
    """Fallback parser for when text extraction puts transactions before section
    headers. Scans lines before header_idx, uses subtotals to exclude future
    installments.
    """
    # Find cardholder(s)
    holders: list[dict[str, str]] = []
    for line in lines:
        m = re.match(r"^([A-Z][A-Z\s]+?)\s*\(final\s+(\d{4})\)", line)
        if m:
            holders.append({"name": m.group(1).strip(), "last_four": m.group(2)})

    # Extract "Total dos lançamentos atuais" to cap transactions
    total_atuais_cents = 0
    for line in lines:
        m = re.search(r"Total dos lan[çc]amentos atuais\s+([\d.,]+)", line)
        if m:
            total_atuais_cents = parse_brl_to_cents(m.group(1))
            break

    # Find scan start: after "Continua..." (if it appears before the header)
    scan_start = 0
    for i in range(header_idx - 1, -1, -1):
        if re.match(r"^Continua\.{3}$", lines[i].strip(), re.IGNORECASE):
            scan_start = i + 1
            break

    # Collect all transaction lines before the header
    all_txs: list[ParsedTransaction] = []
    for i in range(scan_start, header_idx):
        line = lines[i].strip()
        if re.match(r"^\d{2}/\d{2}\s+", line):
            raw_txs = _find_transactions_in_text(line)
            for raw in raw_txs:
                all_txs.append(_build_transaction(raw))

    if len(all_txs) == 0:
        return []

    # Classify into compras vs serviços, using running total to stop at future installments
    compras_section = ParsedCardSection(
        holder_name=holders[0]["name"] if holders else "Titular",
        last_four_digits=holders[0]["last_four"] if holders else "",
        transactions=[],
    )
    servicos_section = ParsedCardSection(
        holder_name="Produtos e Serviços",
        last_four_digits="",
        transactions=[],
    )

    running_total = 0
    for tx in all_txs:
        delta = -tx.amount_cents if tx.is_credit else tx.amount_cents

        # If adding this tx would exceed the known total, we've hit future installments
        if total_atuais_cents > 0 and running_total + delta > total_atuais_cents + 100:
            break
        running_total += delta

        if _looks_like_compra(tx.description, compras_section):
            compras_section.transactions.append(tx)
        else:
            servicos_section.transactions.append(tx)

    result: list[ParsedCardSection] = []
    if len(compras_section.transactions) > 0:
        result.append(compras_section)
    if len(servicos_section.transactions) > 0:
        result.append(servicos_section)
    return result


def _looks_like_compra(description: str, compras_section: ParsedCardSection) -> bool:
    """Heuristic: check if a transaction description looks like a compra (purchase)
    rather than a produto/serviço (fee, installment, credit, etc.)
    """
    upper = description.upper()

    servico_patterns = [
        "PARCELAMEN", "ANUIDADE", "CREDITO", "ESTORNO", "PARC FAT",
        "ENVIO MENS", "ACELERADOR", "IOF", "JUROS", "MULTA", "TARIFA",
        "ENCARGOS",
    ]
    for p in servico_patterns:
        if p in upper:
            return False

    # Match against existing compras in the section (same store name)
    for tx in compras_section.transactions:
        existing_first = re.split(r"\s+", tx.description.upper())[0]
        new_first = re.split(r"\s+", upper)[0]
        if existing_first == new_first and len(existing_first) > 3:
            return True

    return True
