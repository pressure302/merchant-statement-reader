# Merchant Statement Reader

A desktop application for reading merchant processing statements and separating card brand fees from ISO/processor fees.

## What It Does

- Upload a PDF or text merchant statement.
- Shows total processing volume, total fees, effective rate, and estimated processor-only rate.
- Separates card brand/network fees from processor/ISO fees.
- Combines like fees across card brands into one line, while preserving which brands appeared.
- Exports the parsed fee table to CSV.

## Run The App

```powershell
pythonw.exe "Merchant Statement Reader.pyw"
```

The app uses Tkinter for the desktop window and `pdfplumber` for PDF text extraction.

## Adding More Processors

The app is designed so new processors can be added without rewriting the UI:

- `merchant_statement_reader/processors/base.py` defines the processor parser interface.
- `merchant_statement_reader/processors/fiserv.py` contains Fiserv-specific normalization hints.
- `merchant_statement_reader/processors/generic.py` is the fallback parser used for unknown statement formats.

The best way to improve accuracy is to add anonymized example statements and tune parser rules for each processor format.
