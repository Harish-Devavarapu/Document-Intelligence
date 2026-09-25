# Document Intelligence

Invoice intelligence and document OCR application built with Streamlit.

## Workflows
- Invoices: structured extraction, validation and export.
- Other documents/images: OCR presentation without forcing an invoice schema.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

Tesseract OCR is also required. On Debian/Ubuntu:
```bash
sudo apt-get install tesseract-ocr
```

## Streamlit Community Cloud
The repository includes `requirements.txt` for Python dependencies and `packages.txt` for the Tesseract system dependency.
