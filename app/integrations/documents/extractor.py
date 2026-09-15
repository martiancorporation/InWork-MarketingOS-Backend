"""Extract plain text from uploaded files, dispatched by MIME type.

Text formats (txt/markdown/csv/json/xml) need no dependencies. Binary office
formats (pdf/docx/pptx/xlsx) use their libraries via lazy imports, so a missing
library degrades that one type to ``unsupported`` rather than breaking the app.

Security: extracted text is treated as *untrusted data* downstream — any
instructions inside a document are never executed, only summarized. Uploads
are attacker-supplied, so size is bounded twice: ``_guard_zip_bomb`` rejects a
zip-based format whose *uncompressed* payload is absurd (the 20 MB upload cap
alone doesn't bound that), and ``_CappedJoiner`` bounds the extracted text.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass

from app.models.enums import SourceStatus

logger = logging.getLogger("app.documents")

# docx/pptx/xlsx are zip archives. python-docx/python-pptx hand the whole
# archive to lxml, which inflates it into a DOM *before* any text-length cap
# can apply — so a 20 MB upload that expands to gigabytes is a memory DoS on
# the worker. Reject on total uncompressed size, and on an extreme
# compression ratio (the classic bomb signature) even when the absolute total
# looks acceptable.
_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024  # 512 MB
_MAX_COMPRESSION_RATIO = 200

_TEXT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/xml",
    "text/xml",
    "text/html",
}


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    status: str  # SourceStatus value
    error: str | None = None


def extract_text(
    data: bytes, mime_type: str | None, filename: str = "", *, max_chars: int | None = None
) -> ExtractionResult:
    """``max_chars`` bounds how much text the office-format parsers
    (PDF/docx/pptx/xlsx) will accumulate *while parsing*, not just afterward —
    a 20MB upload can still expand to a much larger in-memory text
    representation (a dense spreadsheet, a PDF with thousands of pages), so
    stopping only after the fact still pays the full parse/memory cost of a
    pathological file. Defaults to the same ceiling
    ``IngestionService``/``ContextService`` already truncate to post-hoc.
    """
    if max_chars is None:
        from app.core.config import get_settings

        max_chars = get_settings().intelligence.max_document_chars
    mime = (mime_type or "").split(";")[0].strip().lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if mime in _TEXT_TYPES or ext in {"txt", "md", "markdown", "json", "xml", "html", "htm"}:
            return _ok(_decode(data))
        if mime == "text/csv" or ext in {"csv", "tsv"}:
            return _ok(_csv(data))
        if mime == "application/pdf" or ext == "pdf":
            return _ok(_pdf(data, max_chars))
        if ext == "docx" or "wordprocessingml" in mime:
            return _ok(_docx(data, max_chars))
        if ext == "pptx" or "presentationml" in mime:
            return _ok(_pptx(data, max_chars))
        if ext in {"xlsx", "xlsm"} or "spreadsheetml" in mime:
            return _ok(_xlsx(data, max_chars))
        if mime.startswith("image/"):
            # Image OCR/vision extraction is a later enhancement.
            return ExtractionResult(
                "", SourceStatus.unsupported.value, "image extraction not enabled"
            )
        # Last resort: try to decode as text if it looks textual.
        decoded = _decode(data)
        if decoded.strip():
            return _ok(decoded)
        return ExtractionResult(
            "", SourceStatus.unsupported.value, f"unsupported type: {mime or ext}"
        )
    except _MissingDep as exc:
        return ExtractionResult("", SourceStatus.unsupported.value, str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad file must not break a build
        logger.warning("Extraction failed for %s (%s): %s", filename, mime, exc)
        return ExtractionResult("", SourceStatus.failed.value, str(exc)[:300])


def _ok(text: str) -> ExtractionResult:
    return ExtractionResult(text.strip(), SourceStatus.extracted.value)


class _MissingDep(Exception):
    pass


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _csv(data: bytes) -> str:
    rows = csv.reader(io.StringIO(_decode(data)))
    return "\n".join(", ".join(cell for cell in row) for row in rows)


def _guard_zip_bomb(data: bytes) -> None:
    """Reject a zip-based document that would inflate absurdly.

    Cheap: the central directory carries each entry's declared uncompressed
    size, so this reads metadata only — nothing is decompressed here. Runs
    before handing the archive to python-docx/python-pptx/openpyxl, which
    would otherwise inflate it in full. A malformed archive is left alone;
    the parser will raise and ``extract_text`` degrades it to ``failed``.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile:
        return
    total = sum(info.file_size for info in infos)
    if total > _MAX_UNCOMPRESSED_BYTES:
        raise ValueError(f"archive expands to {total} bytes, over the extraction limit")
    if data and total / max(len(data), 1) > _MAX_COMPRESSION_RATIO:
        raise ValueError("archive compression ratio looks like a zip bomb")


class _CappedJoiner:
    """Accumulate text parts, stopping once ``max_chars`` is reached.

    Bounds the *extracted text* a document can produce, so a pathological file
    (thousands of PDF pages, a densely-populated spreadsheet) can't blow up
    memory downstream. Note this bounds the accumulation loop, not the
    parser's own upfront work — see ``_guard_zip_bomb`` for that half.
    """

    def __init__(self, max_chars: int, sep: str = "\n") -> None:
        self._max_chars = max_chars
        self._sep = sep
        self._parts: list[str] = []
        self._total = 0

    @property
    def done(self) -> bool:
        return self._total >= self._max_chars

    def add(self, part: str | None) -> None:
        if not part or self.done:
            return
        # Slice to the remaining budget rather than taking the whole part and
        # only refusing the *next* one: a single part is itself unbounded (one
        # PDF page, one spreadsheet row), so appending it whole would let the
        # result overshoot the cap by an arbitrary amount.
        remaining = self._max_chars - self._total
        chunk = part[:remaining]
        self._parts.append(chunk)
        self._total += len(chunk)

    def result(self) -> str:
        return self._sep.join(self._parts)


def _pdf(data: bytes, max_chars: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise _MissingDep("pypdf not installed") from exc
    reader = PdfReader(io.BytesIO(data))
    joiner = _CappedJoiner(max_chars, sep="\n\n")
    for page in reader.pages:
        if joiner.done:
            break
        joiner.add(page.extract_text() or "")
    return joiner.result()


def _docx(data: bytes, max_chars: int) -> str:
    try:
        import docx
    except ImportError as exc:
        raise _MissingDep("python-docx not installed") from exc
    _guard_zip_bomb(data)
    document = docx.Document(io.BytesIO(data))
    joiner = _CappedJoiner(max_chars)
    for p in document.paragraphs:
        if joiner.done:
            break
        joiner.add(p.text)
    return joiner.result()


def _pptx(data: bytes, max_chars: int) -> str:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise _MissingDep("python-pptx not installed") from exc
    _guard_zip_bomb(data)
    prs = Presentation(io.BytesIO(data))
    joiner = _CappedJoiner(max_chars)
    for slide in prs.slides:
        if joiner.done:
            break
        for shape in slide.shapes:
            if joiner.done:
                break
            if shape.has_text_frame:
                joiner.add(shape.text_frame.text)
    return joiner.result()


def _xlsx(data: bytes, max_chars: int) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise _MissingDep("openpyxl not installed") from exc
    _guard_zip_bomb(data)
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    joiner = _CappedJoiner(max_chars)
    for ws in wb.worksheets:
        if joiner.done:
            break
        joiner.add(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if joiner.done:
                break
            cells = [str(c) for c in row if c is not None]
            if cells:
                joiner.add(", ".join(cells))
    return joiner.result()
