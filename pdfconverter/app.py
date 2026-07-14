"""Interface gráfica do Conversor de PDF (CustomTkinter).

Tela única com botões grandes para cada ferramenta. As conversões rodam em
uma thread separada para não travar a janela, com barra de progresso.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__
from .core import convert, images, organize

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# Tipos de arquivo reutilizados nos diálogos.
FT_PDF = [("Arquivos PDF", "*.pdf")]
FT_IMAGES = [("Imagens", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.gif")]
FT_OFFICE = [("Documentos do Office", "*.docx *.doc *.xlsx *.xls *.pptx *.ppt *.rtf *.odt")]


def resource_path(rel: str) -> str:
    """Resolve o caminho de um recurso, dentro ou fora do bundle do PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel)


def open_in_explorer(path: str) -> None:
    """Abre a pasta que contém o arquivo/pasta no gerenciador de arquivos."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if platform.system() == "Windows":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Conversor de PDF  v{__version__}")
        self.geometry("780x640")
        self.minsize(680, 560)

        # Ícone da janela (.ico funciona no Windows, que é o alvo do executável).
        try:
            self.iconbitmap(resource_path(os.path.join("assets", "icon.ico")))
        except Exception:
            pass

        self._busy = False
        self._cards: list[ctk.CTkFrame] = []

        self._build_header()
        self._build_grid()
        self._build_status_bar()

    # ------------------------------------------------------------------ UI
    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=("#1f6aa5", "#1f6aa5"))
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="Conversor de PDF",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color="white",
        ).pack(anchor="w", padx=24, pady=(18, 2))
        ctk.CTkLabel(
            header,
            text="Escolha uma ferramenta abaixo. Funciona com arquivos grandes e pequenos.",
            font=ctk.CTkFont(size=13),
            text_color="white",
        ).pack(anchor="w", padx=24, pady=(0, 18))

    def _build_grid(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        container.grid_columnconfigure((0, 1), weight=1, uniform="col")

        # (emoji, título, descrição, handler, destaque?)
        tasks = [
            ("📊", "PDF → Excel", "Extrai tabelas do PDF para uma planilha .xlsx", self.task_pdf_to_excel, True),
            ("📝", "PDF → Word", "Converte o PDF em documento editável .docx", self.task_pdf_to_word, False),
            ("🖼️", "Imagens → PDF", "Junta várias imagens (JPG/PNG) em um PDF", self.task_images_to_pdf, False),
            ("🏞️", "PDF → Imagens", "Salva cada página do PDF como imagem", self.task_pdf_to_images, False),
            ("📄", "Office → PDF", "Word, Excel ou PowerPoint para PDF", self.task_office_to_pdf, False),
            ("🔗", "Juntar PDFs", "Combina vários PDFs em um só arquivo", self.task_merge, False),
            ("✂️", "Dividir PDF", "Separa o PDF em uma página por arquivo", self.task_split, False),
            ("🗜️", "Comprimir PDF", "Reduz o tamanho do arquivo PDF", self.task_compress, False),
        ]

        for i, (emoji, title, desc, handler, highlight) in enumerate(tasks):
            card = self._make_card(container, emoji, title, desc, handler, highlight)
            card.grid(row=i // 2, column=i % 2, padx=8, pady=8, sticky="nsew")
            self._cards.append(card)

    def _make_card(self, parent, emoji, title, desc, handler, highlight):
        border = ("#1f6aa5", "#1f6aa5") if highlight else ("#d0d0d0", "#3a3a3a")
        card = ctk.CTkFrame(parent, corner_radius=12, border_width=2, border_color=border)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text=emoji, font=ctk.CTkFont(size=30)).grid(
            row=0, column=0, rowspan=2, padx=(16, 8), pady=16
        )
        title_text = title + ("   ⭐ mais usado" if highlight else "")
        ctk.CTkLabel(card, text=title_text, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=(0, 12), pady=(16, 0)
        )
        ctk.CTkLabel(
            card, text=desc, font=ctk.CTkFont(size=12), text_color=("gray30", "gray70"),
            wraplength=230, justify="left",
        ).grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 16))

        # O card inteiro é clicável.
        for widget in (card, *card.winfo_children()):
            widget.bind("<Button-1>", lambda e, h=handler: self._launch(h))
            widget.configure(cursor="hand2")
        return card

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=16, pady=(10, 4))
        self.status = ctk.CTkLabel(bar, text="Pronto.", font=ctk.CTkFont(size=12), anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 10))

    # ------------------------------------------------------------- helpers
    def _set_busy(self, busy: bool):
        self._busy = busy
        for card in self._cards:
            state = "disabled" if busy else "normal"
            card.configure(border_color=("gray70", "gray40") if busy else card.cget("border_color"))
            for w in (card, *card.winfo_children()):
                w.configure(cursor="watch" if busy else "hand2")
        self.configure(cursor="watch" if busy else "")

    def _progress(self, current: int, total: int, message: str):
        """Callback chamado pela thread de trabalho. Marshala para a UI."""
        def update():
            frac = (current / total) if total else 0
            self.progress.set(max(0.02, min(1.0, frac)))
            self.status.configure(text=message)
        self.after(0, update)

    def _launch(self, handler):
        """Pede os arquivos (na thread da UI) e dispara o trabalho pesado."""
        if self._busy:
            return
        try:
            job = handler()  # retorna um callable (worker) ou None se cancelado
        except Exception as exc:  # erro ao montar o trabalho
            messagebox.showerror("Erro", str(exc))
            return
        if job is None:
            return

        self._set_busy(True)
        self.progress.set(0.02)
        self.status.configure(text="Processando...")

        def run():
            try:
                result = job()
                self.after(0, lambda: self._on_success(result))
            except Exception as exc:
                tb = traceback.format_exc()
                self.after(0, lambda: self._on_error(exc, tb))

        threading.Thread(target=run, daemon=True).start()

    def _on_success(self, result):
        self._set_busy(False)
        self.progress.set(1.0)
        self.status.configure(text="Concluído com sucesso.")
        message, path = result
        if messagebox.askyesno("Concluído", f"{message}\n\nDeseja abrir a pasta?"):
            open_in_explorer(path)

    def _on_error(self, exc, tb):
        self._set_busy(False)
        self.progress.set(0)
        self.status.configure(text="Erro na conversão.")
        messagebox.showerror("Erro", f"Não foi possível concluir:\n\n{exc}")

    # ------------------------------------------------------------- tarefas
    # Cada tarefa: pede arquivos, devolve um worker sem argumentos que faz o
    # trabalho pesado e retorna (mensagem, caminho_de_saída).

    def task_pdf_to_excel(self):
        src = filedialog.askopenfilename(title="Selecione o PDF", filetypes=FT_PDF)
        if not src:
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar planilha como", defaultextension=".xlsx",
            initialfile=os.path.splitext(os.path.basename(src))[0] + ".xlsx",
            filetypes=[("Planilha do Excel", "*.xlsx")],
        )
        if not out:
            return None

        def worker():
            _, n = convert.pdf_to_excel(src, out, self._progress)
            msg = (f"Planilha criada com {n} tabela(s) encontrada(s)."
                   if n else "PDF sem tabelas: o texto foi extraído para a planilha.")
            return msg, out
        return worker

    def task_pdf_to_word(self):
        src = filedialog.askopenfilename(title="Selecione o PDF", filetypes=FT_PDF)
        if not src:
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar documento como", defaultextension=".docx",
            initialfile=os.path.splitext(os.path.basename(src))[0] + ".docx",
            filetypes=[("Documento do Word", "*.docx")],
        )
        if not out:
            return None

        def worker():
            convert.pdf_to_word(src, out, self._progress)
            return "Documento do Word criado com sucesso.", out
        return worker

    def task_images_to_pdf(self):
        srcs = filedialog.askopenfilenames(title="Selecione as imagens", filetypes=FT_IMAGES)
        if not srcs:
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar PDF como", defaultextension=".pdf",
            initialfile="imagens.pdf", filetypes=FT_PDF,
        )
        if not out:
            return None

        def worker():
            images.images_to_pdf(list(srcs), out, self._progress)
            return f"PDF criado com {len(srcs)} imagem(ns).", out
        return worker

    def task_pdf_to_images(self):
        src = filedialog.askopenfilename(title="Selecione o PDF", filetypes=FT_PDF)
        if not src:
            return None
        out_dir = filedialog.askdirectory(title="Escolha a pasta para salvar as imagens")
        if not out_dir:
            return None

        def worker():
            saved = images.pdf_to_images(src, out_dir, progress=self._progress)
            return f"{len(saved)} imagem(ns) salva(s).", out_dir
        return worker

    def task_office_to_pdf(self):
        src = filedialog.askopenfilename(
            title="Selecione o arquivo do Office", filetypes=FT_OFFICE
        )
        if not src:
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar PDF como", defaultextension=".pdf",
            initialfile=os.path.splitext(os.path.basename(src))[0] + ".pdf",
            filetypes=FT_PDF,
        )
        if not out:
            return None

        def worker():
            convert.office_to_pdf(src, out, self._progress)
            return "PDF criado com sucesso.", out
        return worker

    def task_merge(self):
        srcs = filedialog.askopenfilenames(title="Selecione os PDFs (na ordem desejada)", filetypes=FT_PDF)
        if not srcs or len(srcs) < 2:
            if srcs:
                messagebox.showinfo("Atenção", "Selecione pelo menos dois PDFs.")
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar PDF combinado como", defaultextension=".pdf",
            initialfile="pdf_combinado.pdf", filetypes=FT_PDF,
        )
        if not out:
            return None

        def worker():
            organize.merge_pdfs(list(srcs), out, self._progress)
            return f"{len(srcs)} PDFs combinados.", out
        return worker

    def task_split(self):
        src = filedialog.askopenfilename(title="Selecione o PDF", filetypes=FT_PDF)
        if not src:
            return None
        out_dir = filedialog.askdirectory(title="Escolha a pasta para salvar as páginas")
        if not out_dir:
            return None

        def worker():
            saved = organize.split_pdf(src, out_dir, self._progress)
            return f"{len(saved)} página(s) salva(s) em arquivos separados.", out_dir
        return worker

    def task_compress(self):
        src = filedialog.askopenfilename(title="Selecione o PDF", filetypes=FT_PDF)
        if not src:
            return None
        out = filedialog.asksaveasfilename(
            title="Salvar PDF comprimido como", defaultextension=".pdf",
            initialfile=os.path.splitext(os.path.basename(src))[0] + "_comprimido.pdf",
            filetypes=FT_PDF,
        )
        if not out:
            return None

        def worker():
            _, orig, final = organize.compress_pdf(src, out, self._progress)
            pct = (1 - final / orig) * 100 if orig else 0
            return (f"Compressão concluída: {orig/1e6:.1f} MB → {final/1e6:.1f} MB "
                    f"({pct:.0f}% menor)."), out
        return worker


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
