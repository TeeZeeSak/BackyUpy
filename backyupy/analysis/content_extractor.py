"""Best-effort, bounded text extraction for content-aware classification.

Extraction is deliberately limited: callers pass a maximum character budget and
a maximum page count, and only files whose *metadata* suggests they could be
important are ever opened. Optional third-party libraries are imported lazily;
when absent, the extractor degrades to plain-text reading or returns ``None``
rather than raising, so the application keeps working with no optional deps.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass

from backyupy.config import CODE_EXTENSIONS, DOCUMENT_EXTENSIONS
from backyupy.utils import looks_binary, path_basename, path_extension, safe_read_text

#: Extensions the extractor knows how to handle.
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".reg"}
CODE_EXTENSIONS_SET = set(CODE_EXTENSIONS)
DOCUMENT_EXTENSIONS_SET = set(DOCUMENT_EXTENSIONS)

#: Extensions that are never opened for content (media/binaries).
NEVER_INSPECT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".webp",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mp3", ".wav", ".flac", ".m4a",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".exe", ".dll", ".msi", ".iso",
    ".pfx", ".p12", ".key", ".pem", ".jks", ".keystore", ".ppk",
}

#: Files whose contents must never be read, regardless of size.
SECRET_FILENAMES = {
    ".env", "credentials", "credentials.json", "id_rsa", "id_ed25519",
    "id_ecdsa", "id_dsa", "known_hosts", ".netrc", "secrets.json",
    "service-account.json", "token.json", "master.key",
}


@dataclass
class ExtractionResult:
    """The bounded text extracted from a file plus provenance metadata."""

    text: str
    truncated: bool
    method: str
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether any usable text was produced."""
        return bool(self.text) and not self.error


@dataclass
class ContentExtractor:
    """Extract bounded text from supported document types."""

    max_chars: int = 8000
    max_pages: int = 20

    def is_supported(self, path: str) -> bool:
        """Return ``True`` when *path* has an extractable extension."""
        extension = path_extension(path)
        if extension in NEVER_INSPECT:
            return False
        return extension in (
            TEXT_EXTENSIONS | CODE_EXTENSIONS_SET | DOCUMENT_EXTENSIONS_SET
        )

    def should_refuse(self, path: str) -> str:
        """Return a non-empty reason when a file must not be read.

        This is the *privacy guard*: secrets and private keys are never read
        into memory for LLM classification, even when the file type is
        technically extractable.
        """
        name = path_basename(path).casefold()
        extension = path_extension(path)
        if name in SECRET_FILENAMES:
            return f"refusing to read secret file '{path_basename(path)}'"
        if extension in {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk"}:
            return "refusing to read key material"
        if extension == ".ssh" or ".ssh" in path.replace("\\", "/").split("/"):
            return "refusing to read SSH material"
        return ""

    def extract(self, path: str, *, max_bytes: int | None = None) -> ExtractionResult:
        """Extract bounded text from *path*.

        ``max_bytes`` caps how much is read from disk before character
        truncation; it defaults to a generous multiple of :attr:`max_chars`.
        """
        refusal = self.should_refuse(path)
        if refusal:
            return ExtractionResult("", False, "refused", refusal)

        if not self.is_supported(path):
            return ExtractionResult("", False, "unsupported", "unsupported file type")

        byte_budget = max_bytes or max(self.max_chars * 4, 64 * 1024)
        extension = path_extension(path)

        try:
            if extension in {".pdf"}:
                return self._extract_pdf(path)
            if extension in {".docx"}:
                return self._extract_docx(path)
            if extension in {".xlsx"}:
                return self._extract_xlsx(path)
            if extension in {".xls"}:
                return self._extract_legacy_office(path)
            if extension in {".doc", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"}:
                return self._extract_legacy_office(path)
            if extension in {".csv", ".tsv"}:
                return self._extract_delimited(path, extension, byte_budget)
            return self._extract_text(path, byte_budget)
        except Exception as exc:  # noqa: BLE001 - extraction is best-effort
            return ExtractionResult("", False, "error", str(exc)[:200])

    # -- plain text ------------------------------------------------------
    def _extract_text(self, path: str, byte_budget: int) -> ExtractionResult:
        if looks_binary(path):
            return ExtractionResult("", False, "binary", "binary content detected")
        text, truncated = safe_read_text(path, max_bytes=byte_budget)
        text = self._truncate(text)
        return ExtractionResult(text, truncated, "text")

    def _extract_delimited(self, path: str, extension: str, byte_budget: int) -> ExtractionResult:
        text, truncated = safe_read_text(path, max_bytes=byte_budget)
        delimiter = "\t" if extension == ".tsv" else ","
        rows: list[str] = []
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            for index, row in enumerate(reader):
                if index > 200:
                    truncated = True
                    break
                rows.append(" | ".join(cell.strip() for cell in row[:20]))
        except csv.Error:
            return ExtractionResult(self._truncate(text), truncated, "text")
        joined = "\n".join(rows)
        return ExtractionResult(self._truncate(joined), truncated, "csv")

    # -- PDF -------------------------------------------------------------
    def _extract_pdf(self, path: str) -> ExtractionResult:
        try:
            from pypdf import PdfReader  # type: ignore import-not-found
        except ImportError:
            return ExtractionResult("", False, "unavailable", "pypdf is not installed")

        reader = PdfReader(path)
        pages: list[str] = []
        truncated = False
        for index, page in enumerate(reader.pages):
            if index >= self.max_pages:
                truncated = True
                break
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - individual pages may be malformed
                pages.append("")
        text = "\n".join(pages)
        return ExtractionResult(self._truncate(text), truncated, "pdf")

    # -- DOCX ------------------------------------------------------------
    def _extract_docx(self, path: str) -> ExtractionResult:
        try:
            import docx  # type: ignore import-not-found
        except ImportError:
            return ExtractionResult("", False, "unavailable", "python-docx is not installed")

        document = docx.Document(path)
        paragraphs: list[str] = []
        truncated = False
        for index, paragraph in enumerate(document.paragraphs):
            if sum(len(p) for p in paragraphs) > self.max_chars:
                truncated = True
                break
            if index > 2000:
                truncated = True
                break
            paragraphs.append(paragraph.text)
        return ExtractionResult(self._truncate("\n".join(paragraphs)), truncated, "docx")

    # -- XLSX ------------------------------------------------------------
    def _extract_xlsx(self, path: str) -> ExtractionResult:
        try:
            import openpyxl  # type: ignore import-not-found
        except ImportError:
            return ExtractionResult("", False, "unavailable", "openpyxl is not installed")

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        lines: list[str] = []
        truncated = False
        try:
            for sheet in workbook.worksheets:
                lines.append(f"# sheet: {sheet.title}")
                for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                    if row_index > 200 or sum(len(line) for line in lines) > self.max_chars:
                        truncated = True
                        break
                    cells = [str(cell) for cell in row if cell is not None]
                    if cells:
                        lines.append(" | ".join(cells))
                if truncated:
                    break
        finally:
            workbook.close()
        return ExtractionResult(self._truncate("\n".join(lines)), truncated, "xlsx")

    # -- legacy / OOXML-without-parser -----------------------------------
    def _extract_legacy_office(self, path: str) -> ExtractionResult:
        """Fallback for formats we cannot parse: read printable runs.

        This extracts ASCII/UTF-8 printable substrings, which is enough to give
        the LLM a weak topical hint without attempting full format parsing.
        """
        extension = path_extension(path)
        if extension in {".pptx", ".odt", ".ods", ".odp"}:
            return self._extract_zip_office(path)
        try:
            with open(path, "rb") as handle:
                raw = handle.read(self.max_chars * 4)
        except OSError as exc:
            return ExtractionResult("", False, "error", str(exc)[:200])
        printable = bytes(byte for byte in raw if 32 <= byte < 127 or byte in (9, 10, 13))
        text = printable.decode("latin-1", errors="replace")
        return ExtractionResult(self._truncate(text), False, "legacy-office")

    def _extract_zip_office(self, path: str) -> ExtractionResult:
        """Extract text from OOXML/ODF containers without a format library."""
        import zipfile

        try:
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith(".xml")]
                chunks: list[str] = []
                for name in names[:40]:
                    if name.endswith("document.xml") or name.endswith("content.xml") or "slide" in name:
                        data = archive.read(name).decode("utf-8", errors="replace")
                        chunks.append(_strip_xml_tags(data))
                    if sum(len(c) for c in chunks) > self.max_chars:
                        break
        except (zipfile.BadZipFile, OSError) as exc:
            return ExtractionResult("", False, "error", str(exc)[:200])
        return ExtractionResult(self._truncate("\n".join(chunks)), False, "zip-office")

    # -- helpers ---------------------------------------------------------
    def _truncate(self, text: str) -> str:
        text = text.strip()
        if len(text) > self.max_chars:
            return text[: self.max_chars] + "\n...[truncated]"
        return text


def _strip_xml_tags(xml: str) -> str:
    """Remove XML tags, keeping only human-readable text runs."""
    import re

    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
