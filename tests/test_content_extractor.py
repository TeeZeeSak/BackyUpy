"""Tests for bounded content extraction and its privacy guard."""

from __future__ import annotations

import pytest

from backyupy.analysis.content_extractor import ContentExtractor


class TestPrivacyGuard:
    def test_refuses_env_file(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("SECRET=1")
        extractor = ContentExtractor()
        assert extractor.should_refuse(str(path))
        result = extractor.extract(str(path))
        assert not result.ok
        assert "refusing" in result.error

    def test_refuses_private_key(self, tmp_path):
        path = tmp_path / "server.pem"
        path.write_text("-----BEGIN PRIVATE KEY-----")
        assert ContentExtractor().should_refuse(str(path))

    def test_refuses_ssh_directory(self):
        assert ContentExtractor().should_refuse(r"C:\Users\Michal\.ssh\id_rsa")


class TestExtraction:
    def test_text_truncated_to_budget(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_text("A" * 100_000)
        result = ContentExtractor(max_chars=500).extract(str(path))
        assert result.ok
        assert len(result.text) <= 500 + len("\n...[truncated]")

    def test_csv_rows_summarised(self, tmp_path):
        path = tmp_path / "data.csv"
        path.write_text("a,b,c\n1,2,3\n4,5,6\n")
        result = ContentExtractor().extract(str(path))
        assert "a | b | c" in result.text

    def test_binary_detected(self, tmp_path):
        path = tmp_path / "bin.dat"
        path.write_bytes(b"\x00\x01\x02\x03" * 100)
        result = ContentExtractor().extract(str(path))
        assert not result.ok

    def test_media_not_supported(self, tmp_path):
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"\xff\xd8\xff")
        extractor = ContentExtractor()
        assert not extractor.is_supported(str(path))
        assert not extractor.extract(str(path)).ok

    def test_pdf_extraction_when_pypdf_present(self, tmp_path):
        pypdf = pytest.importorskip("pypdf")
        # Build a minimal PDF with one text object.
        content = b"BT /F1 24 Tf 72 700 Td (Hello BackyUpy) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
        path = tmp_path / "doc.pdf"
        path.write_bytes(bytes(out))

        result = ContentExtractor().extract(str(path))
        assert result.ok
        assert "BackyUpy" in result.text

    def test_docx_extraction_when_available(self, tmp_path):
        docx = pytest.importorskip("docx")
        document = docx.Document()
        document.add_paragraph("Contract between parties")
        path = tmp_path / "c.docx"
        document.save(str(path))
        result = ContentExtractor().extract(str(path))
        assert result.ok
        assert "Contract" in result.text

    def test_xlsx_extraction_when_available(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet["A1"] = "Invoice"
        sheet["B1"] = 1234
        path = tmp_path / "b.xlsx"
        workbook.save(str(path))
        result = ContentExtractor().extract(str(path))
        assert result.ok
        assert "Invoice" in result.text
