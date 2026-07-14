"""Juntar, dividir e comprimir arquivos PDF."""
from __future__ import annotations

import os


def merge_pdfs(pdf_paths: list[str], output_pdf: str, progress=None) -> str:
    """Junta vários PDFs em um só, na ordem informada."""
    from pypdf import PdfWriter

    total = len(pdf_paths)
    if total < 2:
        raise ValueError("Selecione pelo menos dois PDFs para juntar.")

    writer = PdfWriter()
    try:
        for i, path in enumerate(pdf_paths, start=1):
            if progress:
                progress(i, total, f"Juntando {os.path.basename(path)}")
            writer.append(path)
        with open(output_pdf, "wb") as f:
            writer.write(f)
    finally:
        writer.close()
    return output_pdf


def split_pdf(pdf_path: str, output_dir: str, progress=None) -> list[str]:
    """Divide o PDF em vários arquivos, um por página."""
    from pypdf import PdfReader, PdfWriter

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    saved: list[str] = []

    for i, page in enumerate(reader.pages, start=1):
        if progress:
            progress(i, total, f"Extraindo página {i} de {total}")
        writer = PdfWriter()
        writer.add_page(page)
        out = os.path.join(output_dir, f"{base}_pagina_{i:03d}.pdf")
        with open(out, "wb") as f:
            writer.write(f)
        writer.close()
        saved.append(out)
    return saved


def compress_pdf(pdf_path: str, output_pdf: str, progress=None) -> tuple[str, int, int]:
    """Reduz o tamanho do PDF sem perder conteúdo.

    Retorna (caminho, tamanho_original, tamanho_final) em bytes.
    """
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    if progress:
        progress(0, 1, "Analisando o arquivo...")

    original_size = os.path.getsize(pdf_path)
    with fitz.open(pdf_path) as doc:
        if progress:
            progress(1, 1, "Otimizando e salvando...")
        doc.save(
            output_pdf,
            garbage=4,        # remove objetos não utilizados
            deflate=True,     # comprime fluxos de dados
            deflate_images=True,
            deflate_fonts=True,
            clean=True,       # limpa estruturas redundantes
        )
    final_size = os.path.getsize(output_pdf)
    return output_pdf, original_size, final_size
