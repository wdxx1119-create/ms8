from __future__ import annotations

import argparse
import re
from pathlib import Path


def _preview(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "…"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Parse one authorized local text file with the MS8 Absorb parser "
            "without submitting it to canonical memory."
        )
    )
    parser.add_argument("path", type=Path, help="Path to an authorized .txt file.")
    parser.add_argument(
        "--show-preview",
        action="store_true",
        help="Print a short content preview. Disabled by default to reduce accidental disclosure.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=240,
        help="Maximum preview length when --show-preview is used (default: 240).",
    )
    args = parser.parse_args()

    source = args.path.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"File does not exist: {source}")
    if source.suffix.lower() != ".txt":
        raise SystemExit("This minimal example accepts only .txt files.")
    if args.preview_chars < 1 or args.preview_chars > 2_000:
        raise SystemExit("--preview-chars must be between 1 and 2000.")

    from ms8.absorb.parser import parse_document

    document = parse_document(source)
    print(f"source: {document.source_path}")
    print(f"file_type: {document.file_type}")
    print(f"parse_status: {document.parse_status}")
    print(f"title: {document.title}")
    print(f"content_hash: {document.content_hash}")
    print(f"content_chars: {len(document.content_text)}")
    if document.error:
        print(f"error: {document.error}")

    if args.show_preview and document.content_text:
        print(f"preview: {_preview(document.content_text, args.preview_chars)}")

    print("submitted_to_ms8: false")
    print("Parsing does not grant governance approval or create an accepted memory record.")
    return 0 if document.parse_status in {"parsed", "empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
