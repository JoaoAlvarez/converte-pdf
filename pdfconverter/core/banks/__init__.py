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
from .bradesco import parse_bradesco_statement
from .itau import parse_itau_statement
from .nubank import parse_nubank_statement
from .mercadopago import parse_mercadopago_statement


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


# Registro de parsers disponíveis por banco.
_PARSERS = {
    "mercadopago": parse_mercadopago_statement,
    "nubank": parse_nubank_statement,
    "picpay": parse_picpay_statement,
    "bradesco": parse_bradesco_statement,
    "itau": parse_itau_statement,
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


def _text_mupdf_sorted(pdf_path: str, password: str | None) -> str:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(pdf_path)
    try:
        if doc.needs_pass:
            doc.authenticate(password or "")
        return "\n".join(page.get_text("text", sort=True) for page in doc)
    finally:
        doc.close()


def _text_variants(pdf_path: str, password: str | None):
    """Gera variantes de texto (extratores diferentes servem a bancos diferentes)."""
    # 1) pdfplumber consciente de colunas (bom p/ PicPay e faturas de 2 colunas).
    try:
        yield statement_text(pdf_path, password)
    except Exception:
        pass
    # 2) PyMuPDF em ordem geométrica (bom p/ Nubank).
    try:
        yield _text_mupdf_sorted(pdf_path, password)
    except Exception:
        pass


def reconciled_amount_cents(st: ParsedStatement) -> int:
    """Soma dos lançamentos considerando crédito como negativo."""
    return sum(
        (-t.amount_cents if t.is_credit else t.amount_cents)
        for s in st.sections
        for t in s.transactions
    )


def best_statement(pdf_path: str, password: str | None = None,
                   tol_cents: int = 500) -> ParsedStatement | None:
    """Devolve o ParsedStatement de fatura APENAS se a soma dos lançamentos
    reconcilia com o total da fatura (dentro de ``tol_cents``). Caso contrário
    devolve None, e o conversor usa o método genérico. Essa trava evita entregar
    uma fatura estruturada incompleta ou incorreta.
    """
    best: ParsedStatement | None = None
    best_err: int | None = None
    for text in _text_variants(pdf_path, password):
        try:
            st = parse_statement(text)
        except Exception:
            continue
        if not st or st.total_cents <= 0:
            continue
        soma = sum(t.amount_cents for s in st.sections for t in s.transactions)
        err = min(abs(soma - st.total_cents),
                  abs(reconciled_amount_cents(st) - st.total_cents))
        if err <= tol_cents and (best_err is None or err < best_err):
            best, best_err = st, err
    return best
