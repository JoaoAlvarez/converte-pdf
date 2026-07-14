"""Parser posicional do extrato de conta corrente do Itaú.

Diferente da fatura de cartão (core/banks/), o extrato de conta tem colunas em
posições fixas: data, descrição, entradas, saídas e saldo. Este parser usa a
coordenada x de cada palavra para separar os valores nas colunas certas e
consolida todas as páginas em uma única lista de lançamentos.
"""
from __future__ import annotations

import re

VALUE_RE = re.compile(r"^\d[\d.]*,\d{2}-?$")
DATE_COL_RE = re.compile(r"\d{2}/\d{2}(?:/\d{2})?")
DATE_GLUED_RE = re.compile(r"(?<!\d)(\d{2}/\d{2})(?!\d)")

# Faixas de x (em pontos) de cada coluna, observadas no layout do extrato.
X_DATE_MAX = 190
X_DESC_MIN, X_DESC_MAX = 195, 360
X_ENTRADA = (350, 415)
X_SAIDA = (415, 495)
X_SALDO_MIN = 495


def _brl(value: str) -> float | None:
    if not value:
        return None
    v = value.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _page_rows(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= 3:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            lines.append(cur)
            cur, top = [w], w["top"]
    if cur:
        lines.append(cur)
    return lines


def parse(pdf_path: str, password: str | None = None):
    """Devolve (lançamentos, encontrou) — lançamentos é uma lista de dicts com
    data, descricao, entrada, saida, saldo. Devolve ([], False) se o PDF não
    parece um extrato do Itaú ou se a separação por colunas não funcionou."""
    import pdfplumber

    rows = []
    total_values = 0
    classified_values = 0
    last_date = ""
    saw_header = False

    with pdfplumber.open(pdf_path, password=password) as pdf:
        # Detecção: precisa parecer um extrato mensal com as colunas do Itaú.
        head = "\n".join((pdf.pages[i].extract_text() or "") for i in range(min(3, len(pdf.pages))))
        low = head.lower()
        if "extrato" not in low or "saídas" not in low or "saldo" not in low:
            return [], False

        for page in pdf.pages:
            in_table = False
            for ln in _page_rows(page):
                joined = " ".join(w["text"] for w in ln).lower()
                if "descrição" in joined and ("saídas" in joined or "entradas" in joined):
                    in_table = True
                    saw_header = True
                    continue
                if not in_table:
                    continue

                date = ""
                desc_parts = []
                entrada = saida = saldo = ""
                for w in ln:
                    x, t = w["x0"], w["text"]
                    if x < X_DATE_MAX and re.fullmatch(r"\d{2}/\d{2}(?:/\d{2})?", t):
                        date = t[:5]
                    elif VALUE_RE.match(t):
                        total_values += 1
                        if X_ENTRADA[0] <= x < X_ENTRADA[1]:
                            entrada = t
                            classified_values += 1
                        elif X_SAIDA[0] <= x < X_SAIDA[1]:
                            saida = t.rstrip("-")
                            classified_values += 1
                        elif x >= X_SALDO_MIN:
                            saldo = t
                            classified_values += 1
                        else:
                            desc_parts.append(t)
                    elif X_DESC_MIN <= x < X_DESC_MAX:
                        desc_parts.append(t)

                desc = " ".join(desc_parts).strip()
                if not date:
                    m = DATE_GLUED_RE.search(desc)
                    if m:
                        date = m.group(1)
                        desc = (desc[: m.start()] + desc[m.end():]).strip()

                if not (entrada or saida or saldo):
                    continue  # linha sem valor: cabeçalho/endereço/ruído
                if desc.lower().startswith(("descrição", "extrato", "saldo em", "minha conta")):
                    continue

                if date:
                    last_date = date
                rows.append({
                    "data": date or last_date,
                    "descricao": desc,
                    "entrada": _brl(entrada),
                    "saida": _brl(saida),
                    "saldo": _brl(saldo),
                })

    # Sanidade: se quase nenhum valor caiu nas colunas esperadas, as faixas de x
    # não batem com este PDF — melhor deixar o método genérico assumir.
    if not saw_header or not rows:
        return [], False
    if total_values and classified_values / total_values < 0.5:
        return [], False
    return rows, True
