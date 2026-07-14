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


def pdf_to_excel(pdf_path: str, output_xlsx: str, progress=None) -> tuple[str, int]:
    """Converte um PDF em planilha do Excel (.xlsx).

    Detecta tabelas em cada página com o pdfplumber e escreve cada uma em uma
    aba. Quando nenhuma tabela é encontrada, cai para um modo de texto, em que
    cada linha do PDF vira uma linha da planilha — assim o usuário nunca fica
    com uma planilha vazia.

    Retorna (caminho, quantidade_de_tabelas_encontradas).
    """
    import pdfplumber
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)  # começa sem abas; criamos conforme o conteúdo
    tables_found = 0
    sheet_index = 0

    def _new_sheet(title: str):
        # Nomes de aba no Excel: máx. 31 caracteres e sem caracteres proibidos.
        safe = "".join(c for c in title if c not in r"[]:*?/\\")[:31] or "Planilha"
        return wb.create_sheet(title=safe)

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for page_no, page in enumerate(pdf.pages, start=1):
            if progress:
                progress(page_no, total, f"Analisando página {page_no} de {total}")

            tables = page.extract_tables() or []
            for t_no, table in enumerate(tables, start=1):
                if not table:
                    continue
                tables_found += 1
                sheet_index += 1
                ws = _new_sheet(f"Pag{page_no}_Tabela{t_no}")
                for row in table:
                    ws.append([("" if cell is None else str(cell)) for cell in row])

    # Nenhuma tabela detectada: modo texto (uma linha por linha do PDF).
    if tables_found == 0:
        ws = _new_sheet("Texto extraído")
        with pdfplumber.open(pdf_path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for line in text.splitlines():
                    ws.append([line])
                if page_no < len(pdf.pages):
                    ws.append([])  # linha em branco entre páginas

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
