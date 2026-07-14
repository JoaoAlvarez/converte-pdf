"""Parsers de fatura de cartão por banco (portados do projeto gerir-cartao).

Fluxo: extrair o texto do PDF em linhas, detectar o banco emissor e chamar o
parser específico, que devolve um ParsedStatement com transações estruturadas
(data, descrição, parcela, cidade, valor, crédito/débito).

O texto é reconstruído com pdfplumber (uma linha por linha visual do PDF), pois
os parsers esperam cada transação numa única linha "DD/MM descrição valor".
"""
from __future__ import annotations

from .types import ParsedStatement
from .picpay import parse_picpay_statement


import re as _re

_DATE_WORD_RE = _re.compile(r"(?<!\d)(\d{2}/\d{2}(?:/\d{2,4})?)(?!\d)")


def _groups_1d(values, gap):
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _detect_two_columns(words, page_width):
    """Detecta duas colunas de lançamentos lado a lado (faturas de cartão).

    Sinal: datas soltas (dd/mm) agrupadas em duas posições x densas. Devolve o
    x de corte, ou None. Igual à heurística usada em convert.py.
    """
    date_x = [w["x0"] for w in words if _DATE_WORD_RE.fullmatch((w["text"] or "").strip())]
    if len(date_x) < 6:
        return None
    groups = [g for g in _groups_1d(date_x, gap=0.05 * page_width) if len(g) >= 3]
    if len(groups) < 2:
        return None
    groups.sort(key=len, reverse=True)
    g1, g2 = groups[0], groups[1]
    if len(g2) < 0.30 * len(g1):
        return None
    c1, c2 = sum(g1) / len(g1), sum(g2) / len(g2)
    if abs(c2 - c1) < 0.20 * page_width:
        return None
    right_group = g1 if c1 > c2 else g2
    return min(right_group) - 0.03 * page_width


def _rows_to_lines(words, row_tol=3):
    """Agrupa palavras em linhas visuais (por y) e devolve uma string por linha."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    rows, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= row_tol:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            rows.append(cur)
            cur, top = [w], w["top"]
    if cur:
        rows.append(cur)
    return [" ".join(x["text"] for x in sorted(r, key=lambda x: x["x0"])) for r in rows]


def _page_lines(page) -> list[str]:
    """Linhas da página em ordem de leitura, dividindo colunas quando for fatura."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    split_x = _detect_two_columns(words, page.width)
    if split_x is not None:
        left = [w for w in words if w["x0"] < split_x]
        right = [w for w in words if w["x0"] >= split_x]
        return _rows_to_lines(left) + _rows_to_lines(right)
    return _rows_to_lines(words)


def statement_text(pdf_path: str, password: str | None = None) -> str:
    """Extrai o texto do PDF em ordem de leitura (uma linha por linha visual),
    dividindo páginas de duas colunas para não intercalar os lançamentos."""
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for page in pdf.pages:
            lines.extend(_page_lines(page))
    return "\n".join(lines)


def detect_issuer(text: str) -> str:
    if "Mercado Pago" in text or "mercadopago" in text:
        return "mercadopago"
    if any(k in text for k in ("Nu Pagamentos", "Nubank", "NuCel", "nubank")):
        return "nubank"
    if "PicPay" in text or "Picpay" in text:
        return "picpay"
    if "Bradesco" in text or "bradesco" in text:
        return "bradesco"
    if any(k in text for k in ("Itaú", "Itau", "ITAU", "itau")):
        return "itau"
    return "unknown"


# Registro de parsers disponíveis (preenchido conforme forem portados).
_PARSERS = {
    "picpay": parse_picpay_statement,
}


def parse_statement(text: str) -> ParsedStatement | None:
    """Detecta o banco e devolve o ParsedStatement, ou None se não suportado
    ou sem transações."""
    issuer = detect_issuer(text)
    parser = _PARSERS.get(issuer)
    if parser is None:
        # Tenta todos os parsers conhecidos (emissor não detectado).
        for fn in _PARSERS.values():
            st = fn(text)
            if st.sections:
                return st
        return None
    st = parser(text)
    return st if st.sections else None
