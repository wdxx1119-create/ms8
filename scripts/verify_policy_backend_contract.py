#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    module_name = os.getenv("MS8_POLICY_MODULE", "ms8_policy_core")
    os.environ.setdefault("MS8_POLICY_BACKEND", "closed")
    try:
        from ms8.engine_core.policy_engine_loader import get_policy_backend_status, get_policy_engine

        engine = get_policy_engine()
        status = get_policy_backend_status()
        payload = {
            "ok": True,
            "module": module_name,
            "backend": getattr(engine, "backend_name", "unknown"),
            "version": getattr(engine, "backend_version", "unknown"),
            "status": status,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # release-time validator
        print(
            json.dumps(
                {
                    "ok": False,
                    "module": module_name,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
