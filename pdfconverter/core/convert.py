"""Conversões entre PDF e formatos do Office (Word, Excel, PowerPoint)."""
from __future__ import annotations

import os


def pdf_to_word(pdf_path: str, output_docx: str, progress=None) -> str:
    """Converte um PDF em documento do Word (.docx).

    Usa a biblioteca pdf2docx (Python puro) — não precisa do Word instalado.
    """
    from pdf2docx import Converter

    if progress:
        progress(0, 1, "Convertendo PDF para Word (pode levar um tempo)...")

    cv = Converter(pdf_path)
    try:
        cv.convert(output_docx, start=0, end=None)
    finally:
        cv.close()

    if progress:
        progress(1, 1, "Concluído.")
    return output_docx


def _cluster_1d(values, gap):
    """Agrupa valores próximos (dentro de ``gap``) e devolve o centro de cada grupo."""
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _words_to_table(page, row_tol=3, col_gap=14):
    """Reconstrói uma tabela a partir das posições das palavras.

    Usado em PDFs cujas colunas são alinhadas por espaçamento (sem grade
    desenhada), como muitos extratos bancários. Agrupa palavras em linhas pela
    coordenada vertical e em colunas pelos pontos de alinhamento horizontal.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []

    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    rows, cur, cur_top = [], [], None
    for w in words:
        if cur_top is None or abs(w["top"] - cur_top) <= row_tol:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(cur)

    col_starts = _cluster_1d([w["x0"] for w in words], col_gap)
    if not col_starts:
        return []

    table = []
    for row in rows:
        cells = [""] * len(col_starts)
        for w in sorted(row, key=lambda w: w["x0"]):
            ci = min(range(len(col_starts)), key=lambda i: abs(col_starts[i] - w["x0"]))
            cells[ci] = (cells[ci] + " " + w["text"]).strip()
        if any(c.strip() for c in cells):
            table.append(cells)
    return _drop_empty_columns(table)


def _drop_empty_columns(table, min_fill=0.05):
    """Remove colunas quase totalmente vazias (ruído do agrupamento por palavras)."""
    if not table:
        return table
    ncols = max(len(r) for r in table)
    keep = []
    for c in range(ncols):
        filled = sum(1 for r in table if c < len(r) and r[c].strip())
        if filled / len(table) >= min_fill:
            keep.append(c)
    if not keep:
        return table
    return [[(r[c] if c < len(r) else "") for c in keep] for r in table]


def _table_quality(table):
    """Retorna (linhas, colunas, células_preenchidas) para avaliar uma tabela."""
    if not table:
        return 0, 0, 0
    ncols = max(len(r) for r in table)
    filled = sum(1 for r in table for c in r if c and str(c).strip())
    return len(table), ncols, filled


def _is_useful(table):
    """Uma tabela vale a pena se tem ≥2 linhas, ≥2 colunas e conteúdo real."""
    rows, cols, filled = _table_quality(table)
    return rows >= 2 and cols >= 2 and filled >= 4


def pdf_to_excel(pdf_path: str, output_xlsx: str, progress=None) -> tuple[str, int]:
    """Converte um PDF em planilha do Excel (.xlsx).

    Estratégia por página, para lidar com formatos diferentes de extrato:
      1. Tenta a detecção por **grade desenhada** (pdfplumber padrão). Funciona
         bem em extratos com linhas de tabela, como o Banco do Brasil.
      2. Se a grade não produz uma tabela útil, reconstrói a tabela pelas
         **posições das palavras** — resolve extratos alinhados por espaçamento,
         como o Itaú.
      3. Se nada der certo na página, extrai o texto linha a linha, para o
         usuário nunca ficar com uma planilha vazia.

    Retorna (caminho, quantidade_de_tabelas_encontradas).
    """
    import pdfplumber
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)  # começa sem abas; criamos conforme o conteúdo
    tables_found = 0

    def _new_sheet(title: str):
        # Nomes de aba no Excel: máx. 31 caracteres e sem caracteres proibidos.
        safe = "".join(c for c in title if c not in r"[]:*?/\\")[:31] or "Planilha"
        return wb.create_sheet(title=safe)

    def _write(title, table):
        ws = _new_sheet(title)
        for row in table:
            ws.append([("" if cell is None else str(cell)) for cell in row])

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for page_no, page in enumerate(pdf.pages, start=1):
            if progress:
                progress(page_no, total, f"Analisando página {page_no} de {total}")

            # 1) Grade desenhada.
            grid_tables = [t for t in (page.extract_tables() or []) if _is_useful(t)]
            if grid_tables:
                for t_no, table in enumerate(grid_tables, start=1):
                    tables_found += 1
                    _write(f"Pag{page_no}_Tabela{t_no}", table)
                continue

            # 2) Agrupamento por posição das palavras.
            wt = _words_to_table(page)
            if _is_useful(wt):
                tables_found += 1
                _write(f"Pag{page_no}", wt)
                continue

            # 3) Texto simples desta página (uma linha por linha do PDF).
            text = page.extract_text() or ""
            if text.strip():
                ws = _new_sheet(f"Pag{page_no}_texto")
                for line in text.splitlines():
                    ws.append([line])

    if not wb.sheetnames:
        _new_sheet("Vazio")

    if progress:
        progress(1, 1, "Salvando planilha...")
    wb.save(output_xlsx)
    return output_xlsx, tables_found


# Extensões do Office suportadas na conversão para PDF.
OFFICE_EXTENSIONS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".rtf", ".odt"}


def office_to_pdf(input_path: str, output_pdf: str, progress=None) -> str:
    """Converte Word/Excel/PowerPoint em PDF.

    Estratégia (apenas Windows, que é o alvo do executável):
      1. Usa o Microsoft Office via automação COM, se estiver instalado.
      2. Se não houver Office, tenta o LibreOffice em modo headless.

    Levanta RuntimeError com mensagem amigável se nenhum estiver disponível.
    """
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in OFFICE_EXTENSIONS:
        raise ValueError(f"Formato não suportado: {ext}")

    if progress:
        progress(0, 1, "Preparando conversão...")

    # 1) Microsoft Office via COM (Windows)
    try:
        return _office_com_to_pdf(input_path, output_pdf, ext, progress)
    except _NoOfficeError:
        pass

    # 2) LibreOffice em modo headless
    if _libreoffice_to_pdf(input_path, output_pdf, progress):
        return output_pdf

    raise RuntimeError(
        "Para converter arquivos do Office (Word/Excel/PowerPoint) é preciso ter "
        "o Microsoft Office ou o LibreOffice instalado neste computador."
    )


class _NoOfficeError(Exception):
    """Uso interno: Microsoft Office não está disponível."""


def _office_com_to_pdf(input_path, output_pdf, ext, progress):
    """Automação COM do Microsoft Office. Só funciona no Windows."""
    try:
        import comtypes.client  # type: ignore
    except ImportError:
        raise _NoOfficeError()

    input_path = os.path.abspath(input_path)
    output_pdf = os.path.abspath(output_pdf)
    wdFormatPDF = 17
    xlTypePDF = 0
    ppSaveAsPDF = 32

    if progress:
        progress(0, 1, "Abrindo no Microsoft Office...")

    try:
        if ext in {".docx", ".doc", ".rtf", ".odt"}:
            app = comtypes.client.CreateObject("Word.Application")
            app.Visible = False
            doc = app.Documents.Open(input_path)
            try:
                doc.SaveAs(output_pdf, FileFormat=wdFormatPDF)
            finally:
                doc.Close()
                app.Quit()

        elif ext in {".xlsx", ".xls"}:
            app = comtypes.client.CreateObject("Excel.Application")
            app.Visible = False
            wb = app.Workbooks.Open(input_path)
            try:
                wb.ExportAsFixedFormat(xlTypePDF, output_pdf)
            finally:
                wb.Close(False)
                app.Quit()

        elif ext in {".pptx", ".ppt"}:
            app = comtypes.client.CreateObject("PowerPoint.Application")
            pres = app.Presentations.Open(input_path, WithWindow=False)
            try:
                pres.SaveAs(output_pdf, ppSaveAsPDF)
            finally:
                pres.Close()
                app.Quit()
        else:
            raise _NoOfficeError()
    except OSError:
        # CreateObject falha quando o Office não está instalado.
        raise _NoOfficeError()

    if progress:
        progress(1, 1, "Concluído.")
    return output_pdf


def _libreoffice_to_pdf(input_path, output_pdf, progress) -> bool:
    """Tenta o LibreOffice em modo headless. Retorna True se der certo."""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if not soffice:
        # Caminhos comuns de instalação no Windows.
        for candidate in (
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ):
            if os.path.exists(candidate):
                soffice = candidate
                break
    if not soffice:
        return False

    if progress:
        progress(0, 1, "Convertendo com o LibreOffice...")

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, input_path],
            check=True,
            timeout=600,
        )
        base = os.path.splitext(os.path.basename(input_path))[0]
        produced = os.path.join(tmp, base + ".pdf")
        if not os.path.exists(produced):
            return False
        import shutil as _sh
        _sh.move(produced, output_pdf)

    if progress:
        progress(1, 1, "Concluído.")
    return True
