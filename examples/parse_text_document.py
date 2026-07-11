from __future__ import annotations

import os
import tempfile
from pathlib import Path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ms8-absorb-parser-") as raw_root:
        root = Path(raw_root)
        ms8_home = root / "runtime" / ".ms8"
        os.environ.update(
            {
                "HOME": str(root / "home"),
                "USERPROFILE": str(root / "home"),
                "MS8_HOME": str(ms8_home),
                "MS8_DATA_DIR": str(ms8_home / "data"),
                "MS8_CONFIG_DIR": str(ms8_home / "config"),
                "MS8_LOG_DIR": str(ms8_home / "logs"),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )

        source = root / "authorized-example" / "project-notes.txt"
        source.parent.mkdir(parents=True)
        source.write_text(
            "Synthetic project note for the MS8 parser example.\n"
            "This content is parsed but is not submitted to canonical memory.\n",
            encoding="utf-8",
        )

        # Import only after the isolated environment is defined.
        from ms8.absorb.parser import parse_document

        document = parse_document(source)
        if document.parse_status != "parsed":
            raise RuntimeError(f"parse failed: {document}")
        if document.file_type != ".txt":
            raise RuntimeError(f"unexpected file type: {document.file_type}")
        if len(document.content_hash) != 64:
            raise RuntimeError("expected a SHA-256 content hash")

        print(f"path: {source}")
        print(f"status: {document.parse_status}")
        print(f"type: {document.file_type}")
        print(f"hash: {document.content_hash}")
        print(f"characters: {len(document.content_text)}")
        print("No memory record was written; parsing is not approval.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
