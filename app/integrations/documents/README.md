# `app/integrations/documents`

Text extraction from uploaded files for the ingestion pipeline, dispatched by
MIME type (`extractor.py::extract_text`).

- Text formats (txt/markdown/csv/json/xml/html) need no dependencies.
- Binary office formats use their libraries via **lazy imports** — pdf (`pypdf`),
  docx (`python-docx`), pptx (`python-pptx`), xlsx (`openpyxl`). A missing
  library degrades that one type to `unsupported` rather than breaking the app.
- Images return `unsupported` for now (vision/OCR is a later enhancement).

Extracted text is treated as **untrusted data** downstream: instructions inside
a document are summarized, never executed.
