from __future__ import annotations

import json
from pathlib import Path

from ms8.absorb import parser
from ms8.absorb import search as absorb_search
from ms8.absorb.chunker import estimate_tokens, split_text
from ms8.absorb.incremental_processor import process_pending
from ms8.absorb.parser import parse_document
from ms8.absorb.repository import init_repository
from ms8.absorb.scope import add_allowed_root
from ms8.absorb.spotlight_bootstrap import bootstrap_authorized_roots
from ms8.cli import main


def _last_json_payload(out: str) -> dict:
    start = out.find("{")
    assert start >= 0, out
    return json.loads(out[start:])


def test_absorb_parser_md_txt_json(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    txt = tmp_path / "b.txt"
    js = tmp_path / "c.json"
    md.write_text("# Title\nHello", encoding="utf-8")
    txt.write_text("Plain text", encoding="utf-8")
    js.write_text('{"name": "ms8"}', encoding="utf-8")

    assert parse_document(md).parse_status == "parsed"
    assert parse_document(txt).parse_status == "parsed"
    parsed_json = parse_document(js)
    assert parsed_json.parse_status == "parsed"
    assert '"name": "ms8"' in parsed_json.content_text


def test_absorb_parser_code_comments(tmp_path: Path) -> None:
    py = tmp_path / "tool.py"
    rs = tmp_path / "tool.rs"
    py.write_text('"""module docs"""\n# python comment\n', encoding="utf-8")
    rs.write_text("// rust comment\nfn main() {}\n", encoding="utf-8")

    assert "module docs" in parse_document(py).content_text
    assert "rust comment" in parse_document(rs).content_text


def test_absorb_parser_docx_when_dependency_available(tmp_path: Path) -> None:
    import pytest

    docx = pytest.importorskip("docx")
    path = tmp_path / "note.docx"
    doc = docx.Document()
    doc.add_paragraph("docx absorb paragraph")
    doc.save(path)

    parsed = parse_document(path)
    assert parsed.parse_status == "parsed"
    assert "docx absorb paragraph" in parsed.content_text


def test_absorb_parser_scanned_pdf_reports_ocr_required(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(parser, "_parse_pdf", lambda _path: "")

    parsed = parse_document(pdf)
    assert parsed.parse_status == "ocr_required"
    assert parsed.error in {"ocr_required", "missing_optional_dependency:absorb-ocr"}


def test_absorb_parser_scanned_pdf_uses_ocr_when_available(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(parser, "_parse_pdf", lambda _path: "")
    monkeypatch.setattr(parser, "ocr_pdf", lambda _path: "ocr extracted pdf text")

    parsed = parse_document(pdf)
    assert parsed.parse_status == "parsed"
    assert "ocr extracted" in parsed.content_text


def test_absorb_parser_pdf_missing_pypdf_uses_ocr_when_available(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def _missing_pypdf(_path: Path) -> str:
        raise RuntimeError("missing_optional_dependency:pypdf")

    monkeypatch.setattr(parser, "_parse_pdf", _missing_pypdf)
    monkeypatch.setattr(parser, "ocr_pdf", lambda _path: "ocr fallback without pypdf")

    parsed = parse_document(pdf)
    assert parsed.parse_status == "parsed"
    assert "ocr fallback" in parsed.content_text


def test_absorb_parser_image_uses_optional_ocr(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"not really an image")
    monkeypatch.setattr(parser, "ocr_image", lambda _path: "ocr image text")

    parsed = parse_document(image)
    assert parsed.parse_status == "parsed"
    assert parsed.file_type == ".png"
    assert "ocr image text" in parsed.content_text


def test_absorb_parser_image_reports_ocr_required_when_missing(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "scan.jpg"
    image.write_bytes(b"not really an image")

    def _missing(_path: Path) -> str:
        raise RuntimeError("missing_optional_dependency:absorb-ocr")

    monkeypatch.setattr(parser, "ocr_image", _missing)
    parsed = parse_document(image)
    assert parsed.parse_status == "ocr_required"
    assert "missing_optional_dependency:absorb-ocr" in parsed.error


def test_absorb_chunker_splits_long_text() -> None:
    text = " ".join(["word"] * 1200)
    chunks = split_text(text, max_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1
    assert all(estimate_tokens(chunk) > 0 for chunk in chunks)


def test_absorb_cli_status_uses_temp_home(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    code = main(["absorb", "status"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"authorized_roots": 0' in out
    payload = _last_json_payload(out)
    assert payload["next_actions"] == ["ms8 absorb add <directory>"]


def test_absorb_cli_search_pretty_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("absorb pretty search alpha signal", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    assert main(["absorb", "search", "pretty search alpha", "--pretty"]) == 0
    out = capsys.readouterr().out
    assert "MS8_ABSORB_SEARCH" in out
    assert "matches=" in out
    assert "next_actions:" in out
    assert 'ms8 ask "pretty search alpha"' in out


def test_absorb_search_filters_stale_whoosh_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    init_repository()
    monkeypatch.setattr(
        absorb_search,
        "_whoosh_search",
        lambda query, limit: [
            {
                "chunk_id": "missing_chunk",
                "canonical_path": "/tmp/deleted.md",
                "status": "LOCAL_INDEXED",
                "risk_level": "low",
                "text_preview": "deleted stale match",
                "search_backend": "whoosh",
                "score": 9.0,
            }
        ],
    )

    assert absorb_search.search_chunks("deleted stale match") == []


def test_absorb_cli_review_list_has_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Contact review next action at next@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    assert main(["absorb", "review", "list"]) == 0
    payload = _last_json_payload(capsys.readouterr().out)
    assert payload["pending_review"]
    assert payload["next_actions"][0].startswith("ms8 absorb review approve ")

def test_absorb_cli_autosubmit_toggle(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    assert main(["absorb", "autosubmit", "enable"]) == 0
    assert '"auto_submit_summaries": true' in capsys.readouterr().out
    assert main(["absorb", "autosubmit", "disable"]) == 0
    assert '"auto_submit_summaries": false' in capsys.readouterr().out


def test_absorb_cli_autosubmit_tier(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    assert main(["absorb", "autosubmit", "tier", "REVIEWED_ONLY"]) == 0
    assert '"auto_write_tier": "REVIEWED_ONLY"' in capsys.readouterr().out
    assert main(["absorb", "autosubmit", "status"]) == 0
    assert '"auto_write_tier": "REVIEWED_ONLY"' in capsys.readouterr().out


def test_absorb_cli_autosubmit_run_preview(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("cli auto write preview safe phrase", encoding="utf-8")
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    assert main(["absorb", "autosubmit", "tier", "LOW_RISK_CHUNKS"]) == 0
    capsys.readouterr()

    assert main(["absorb", "autosubmit", "run", "--limit", "5", "--daily-cap", "2"]) == 0
    out = capsys.readouterr().out
    assert '"status": "dry_run"' in out
    assert '"auto_write_tier": "LOW_RISK_CHUNKS"' in out


def test_absorb_cli_autosubmit_rollback_preview(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    assert main(["absorb", "autosubmit", "rollback", "--since-hours", "1"]) == 0
    out = capsys.readouterr().out
    assert '"status": "dry_run"' in out
    assert '"since_hours": 1' in out


def test_absorb_cli_stop_explains_foreground_and_service(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    assert main(["absorb", "stop"]) == 0
    out = capsys.readouterr().out
    assert '"status": "foreground_only"' in out
    assert "ms8 service absorb-remove" in out


def test_absorb_cli_review_bulk_preview(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Contact reviewer at test@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    assert main(["absorb", "review", "approve-all", "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert '"status": "dry_run"' in out
    assert '"action": "approve_all"' in out


def test_absorb_cli_review_restore(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Contact restore at restore@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    from ms8.absorb.repository import list_chunks_by_status
    from ms8.absorb.reviewer import reject_chunk

    chunk_id = list_chunks_by_status(("PENDING_REVIEW",), limit=1)[0]["chunk_id"]
    assert reject_chunk(chunk_id)["ok"] is True
    assert main(["absorb", "review", "restore", chunk_id]) == 0
    out = capsys.readouterr().out
    assert '"status": "restored"' in out
