from __future__ import annotations

import json

from ms8.runtime import repair_compression_if_stale, repair_duplicates_after_compression


def main() -> int:
    comp = repair_compression_if_stale()
    dedupe = repair_duplicates_after_compression()
    out = {"compression_repair": comp, "duplicate_cluster_repair": dedupe}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
