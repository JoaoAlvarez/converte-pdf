# Conversor de PDF

Ferramenta de desktop **para Windows** que converte arquivos PDF e do Office —
pensada para pessoas não técnicas (contadores, advogados, etc.). Basta baixar um
`.exe` e dar dois cliques; **não precisa instalar nada**.

## Ferramentas incluídas

| Ferramenta | O que faz |
|---|---|
| 📊 **PDF → Excel** | Extrai tabelas do PDF para uma planilha `.xlsx` |
| 📝 PDF → Word | Converte o PDF em `.docx` editável |
| 🖼️ Imagens → PDF | Junta várias imagens (JPG/PNG) em um PDF |
| 🏞️ PDF → Imagens | Salva cada página como imagem |
| 📄 Office → PDF | Word, Excel ou PowerPoint para PDF |
| 🔗 Juntar PDFs | Combina vários PDFs em um |
| ✂️ Dividir PDF | Separa em uma página por arquivo |
| 🗜️ Comprimir PDF | Reduz o tamanho do arquivo |

Funciona com arquivos grandes e pequenos — as conversões rodam em segundo
plano, com barra de progresso, sem travar a janela.

## Para o usuário final

1. Acesse a página **Releases** deste repositório no GitHub.
2. Baixe o arquivo **`ConversorPDF.exe`**.
3. Dê dois cliques para abrir. Pronto.

> Observação: **Office → PDF** usa o Microsoft Office ou o LibreOffice instalado
> no computador. As demais ferramentas funcionam de forma independente.

## Como publicar uma nova versão (para o desenvolvedor)

O `.exe` do Windows é gerado automaticamente pelo GitHub Actions — você **não
precisa de um computador com Windows**. Basta criar uma tag de versão:

```bash
git tag v1.0.0
git push origin v1.0.0
```

O GitHub compila o `.exe` em um runner Windows e o publica na página de
**Releases**, pronto para download. Também é possível rodar manualmente pela
aba **Actions → Gerar executável do Windows → Run workflow**.

## Rodando durante o desenvolvimento (opcional)

```bash
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Como funciona por dentro

- **Interface:** [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
- **PDF → Excel:** `pdfplumber` (detecção de tabelas) + `openpyxl`
- **PDF → Word:** `pdf2docx`
- **Imagens / compressão / render:** `PyMuPDF`
- **Juntar / dividir:** `pypdf`
- **Office → PDF:** automação COM do Microsoft Office (ou LibreOffice headless)
- **Empacotamento:** `PyInstaller` (`--onefile`) via GitHub Actions

## Código

```
pdf-converter/
├── main.py                     # ponto de entrada
├── requirements.txt
├── pdfconverter/
│   ├── app.py                  # interface gráfica
│   └── core/
│       ├── images.py           # imagens <-> PDF
│       ├── organize.py         # juntar / dividir / comprimir
│       └── convert.py          # PDF -> Excel/Word, Office -> PDF
└── .github/workflows/build.yml # gera e publica o .exe
```
