"""Document text extraction for the ingestion pipeline."""

from app.integrations.documents.extractor import ExtractionResult, extract_text

__all__ = ["ExtractionResult", "extract_text"]
