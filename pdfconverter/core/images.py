"""Conversões entre imagens e PDF usando PyMuPDF."""
from __future__ import annotations

import os


def _open_fitz():
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24 expõe o nome moderno
    except ImportError:
        import fitz  # fallback para versões antigas
    return fitz


def images_to_pdf(image_paths: list[str], output_pdf: str, progress=None) -> str:
    """Junta várias imagens (JPG/PNG/etc.) em um único PDF, uma por página."""
    fitz = _open_fitz()
    total = len(image_paths)
    if total == 0:
        raise ValueError("Nenhuma imagem selecionada.")

    doc = fitz.open()
    try:
        for i, img_path in enumerate(image_paths, start=1):
            if progress:
                progress(i, total, f"Adicionando {os.path.basename(img_path)}")
            with fitz.open(img_path) as img_doc:
                pdf_bytes = img_doc.convert_to_pdf()
            with fitz.open("pdf", pdf_bytes) as img_pdf:
                doc.insert_pdf(img_pdf)
        doc.save(output_pdf)
    finally:
        doc.close()
    return output_pdf


def pdf_to_images(
    pdf_path: str,
    output_dir: str,
    fmt: str = "png",
    dpi: int = 150,
    progress=None,
) -> list[str]:
    """Converte cada página do PDF em um arquivo de imagem."""
    fitz = _open_fitz()
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    saved: list[str] = []

    with fitz.open(pdf_path) as doc:
        total = doc.page_count
        for i, page in enumerate(doc, start=1):
            if progress:
                progress(i, total, f"Renderizando página {i} de {total}")
            pix = page.get_pixmap(dpi=dpi)
            out = os.path.join(output_dir, f"{base}_pagina_{i:03d}.{fmt}")
            pix.save(out)
            saved.append(out)
    return saved
