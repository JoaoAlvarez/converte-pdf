"""Ponto de entrada do Conversor de PDF.

Executado tanto durante o desenvolvimento (python main.py) quanto pelo
executável gerado pelo PyInstaller.
"""
from pdfconverter.app import main

if __name__ == "__main__":
    main()
