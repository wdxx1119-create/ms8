from __future__ import annotations

import json

from ms8.review_governance import archive_and_sync_review_queue
from ms8.runtime import ensure_runtime_dirs


def main() -> int:
    paths = ensure_runtime_dirs()
    root = paths["root"]
    queue_file = root / "memory" / "auto_memory_review_queue.jsonl"
    archive_dir = root / "memory"
    report_file = paths["health"] / "review_governance_latest.json"
    out = archive_and_sync_review_queue(
        queue_file=queue_file,
        records_file=paths["memories"],
        archive_dir=archive_dir,
        report_file=report_file,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
