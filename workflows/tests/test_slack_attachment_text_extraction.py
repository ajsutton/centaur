from __future__ import annotations

import asyncio
import importlib
import io
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "services" / "workflow-python"),
)


def _load():
    # Some workflow unit tests install module stubs without restoring them.
    # Ensure this test exercises the real shared Slack implementation.
    for name in ("workflows.etl_metrics", "workflows.slack.shared"):
        module = sys.modules.get(name)
        if module is not None and not getattr(module, "__file__", None):
            sys.modules.pop(name)
    return importlib.import_module("workflows.slack_attachment_text_extraction")


def test_extracts_plain_text_and_bounds_output():
    extraction = _load()

    text, metadata = extraction.extract_text(
        b"launch plan\nsecond line",
        filename="plan.md",
        declared_mime_type="text/markdown",
        max_expanded_bytes=1_000,
        max_pages=10,
        max_characters=11,
    )

    assert text == "launch plan"
    assert metadata == {
        "detected_mime_type": "text/plain",
        "character_count": 11,
        "truncated": True,
    }


def test_extracts_pdf_text_by_page():
    pymupdf = pytest.importorskip("pymupdf")
    extraction = _load()
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "Project Atlas launches in September")
    document.new_page()
    payload = document.tobytes()
    document.close()

    text, metadata = extraction.extract_text(
        payload,
        filename="roadmap.pdf",
        declared_mime_type="application/pdf",
        max_expanded_bytes=1_000,
        max_pages=10,
        max_characters=10_000,
    )

    assert "## Page 1" in text
    assert "Project Atlas launches in September" in text
    assert metadata["page_count"] == 2
    assert metadata["pages_without_text"] == [2]
    assert metadata["truncated"] is False


def test_image_only_pdf_is_unsupported_without_ocr():
    pymupdf = pytest.importorskip("pymupdf")
    extraction = _load()
    document = pymupdf.open()
    document.new_page()
    payload = document.tobytes()
    document.close()

    with pytest.raises(extraction.UnsupportedAttachment, match="no_extractable_text"):
        extraction.extract_text(
            payload,
            filename="scan.pdf",
            declared_mime_type="application/pdf",
            max_expanded_bytes=1_000,
            max_pages=10,
            max_characters=10_000,
        )


def test_extracts_docx_paragraphs_and_tables():
    docx = pytest.importorskip("docx")
    extraction = _load()
    document = docx.Document()
    document.add_paragraph("Quarterly roadmap")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Owner"
    table.cell(0, 1).text = "Avery"
    output = io.BytesIO()
    document.save(output)

    text, metadata = extraction.extract_text(
        output.getvalue(),
        filename="roadmap.docx",
        declared_mime_type=extraction.DOCX_MIME_TYPE,
        max_expanded_bytes=10_000_000,
        max_pages=10,
        max_characters=10_000,
    )

    assert "Quarterly roadmap" in text
    assert "Owner\tAvery" in text
    assert metadata["table_count"] == 1


def test_extracts_pptx_slide_text_and_notes():
    pptx = pytest.importorskip("pptx")
    extraction = _load()
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Launch plan"
    slide.placeholders[1].text = "Ship in September"
    slide.notes_slide.notes_text_frame.text = "Confirm with sales"
    output = io.BytesIO()
    presentation.save(output)

    text, metadata = extraction.extract_text(
        output.getvalue(),
        filename="launch.pptx",
        declared_mime_type=extraction.PPTX_MIME_TYPE,
        max_expanded_bytes=10_000_000,
        max_pages=10,
        max_characters=10_000,
    )

    assert "## Slide 1" in text
    assert "Launch plan" in text
    assert "Ship in September" in text
    assert "Confirm with sales" in text
    assert metadata["slide_count"] == 1


def test_rejects_non_slack_download_url_before_creating_client(monkeypatch):
    extraction = _load()
    monkeypatch.setattr(
        extraction,
        "_slack_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not create client")),
    )
    row = {
        "content_bytes": None,
        "size_bytes": 100,
        "url_private": "https://example.com/private.pdf",
        "mimetype": "application/pdf",
        "name": "private.pdf",
    }

    with pytest.raises(extraction.UnsupportedAttachment, match="invalid_download_url"):
        asyncio.run(extraction._attachment_bytes(row, max_download_bytes=1_000))


def test_rejects_html_download_response_for_binary_document(monkeypatch):
    extraction = _load()

    class FakeSlackClient:
        def download_file_bytes(self, _url, *, max_bytes):
            assert max_bytes == 1_000
            return "text/html", b"<html>sign in</html>"

    monkeypatch.setattr(extraction, "_slack_client", FakeSlackClient)
    row = {
        "content_bytes": None,
        "size_bytes": 100,
        "url_private": "https://files.slack.com/files-pri/T/F123/report.pdf",
        "mimetype": "application/pdf",
        "name": "report.pdf",
    }

    with pytest.raises(
        extraction.RetryableDownloadError, match="unexpected Slack file content type"
    ):
        asyncio.run(extraction._attachment_bytes(row, max_download_bytes=1_000))


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_args = None
        self.execute_calls = []

    async def fetch(self, _query, *args):
        self.fetch_args = args
        return self.rows

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))


def test_download_failure_is_persisted_with_retry_backoff(monkeypatch):
    extraction = _load()
    row = {
        "channel_id": "C123",
        "message_ts": "1770000000.000100",
        "slack_file_id": "F123",
        "name": "report.pdf",
        "mimetype": "application/pdf",
        "content_sha256": None,
        "attempt_count": 0,
    }
    pool = FakePool([])

    async def fail_download(_row, *, max_download_bytes):
        assert max_download_bytes == 1_000
        raise extraction.urllib_error.URLError("temporary failure")

    monkeypatch.setattr(extraction, "_attachment_bytes", fail_download)

    result = asyncio.run(
        extraction._extract_and_store(
            pool,
            row,
            extractor_version="1",
            max_download_bytes=1_000,
            max_expanded_bytes=2_000,
            max_pages=10,
            max_characters=10_000,
        )
    )

    assert result == {
        "status": "failed",
        "error_type": "URLError",
        "retry_scheduled": True,
    }
    assert len(pool.execute_calls) == 1
    stored_args = pool.execute_calls[0][1]
    assert stored_args[5] == "failed"
    assert stored_args[8] == 1
    assert stored_args[9] is not None
    assert "temporary failure" in stored_args[10]


def test_retry_backoff_stops_after_max_attempts():
    extraction = _load()
    count, retry_at = extraction._retry_state(
        {"attempt_count": extraction.DEFAULT_MAX_ATTEMPTS - 1},
        max_attempts=extraction.DEFAULT_MAX_ATTEMPTS,
    )

    assert count == extraction.DEFAULT_MAX_ATTEMPTS
    assert retry_at is None


def test_handler_persists_text_without_checkpointing_the_full_content(monkeypatch):
    extraction = _load()
    row = {
        "channel_id": "C123",
        "message_ts": "1770000000.000100",
        "slack_file_id": "F123",
        "name": "notes.txt",
        "mimetype": "text/plain",
        "filetype": "txt",
        "size_bytes": 16,
        "url_private": "",
        "download_status": "downloaded",
        "content_sha256": None,
        "content_bytes": b"confidential plan",
        "updated_at": "2026-07-01T00:00:00Z",
        "attempt_count": 0,
    }
    pool = FakePool([row])
    step_values = []

    async def step(_name, fn, **_kwargs):
        value = fn()
        if hasattr(value, "__await__"):
            value = await value
        step_values.append(value)
        return value

    async def start_workflow(*_args, **_kwargs):
        return {"run_id": "next"}

    context = types.SimpleNamespace(
        _pool=pool,
        run_id="run-1",
        step=step,
        start_workflow=start_workflow,
        log=lambda *_args, **_kwargs: None,
    )

    result = asyncio.run(extraction.handler(extraction.Input(batch_size=2), context))

    assert result["succeeded"] == 1
    assert result["requeued"] is False
    assert step_values == [{"status": "succeeded", "characters": 17}]
    insert_args = pool.execute_calls[0][1]
    assert insert_args[6] == "confidential plan"
    assert len(pool.execute_calls) == 1
    assert pool.execute_calls[0][0].startswith("WITH extraction AS")
    assert "UPDATE slack_sync_message_attachments" in pool.execute_calls[0][0]
