#!/usr/bin/env python3
"""Phase-0 skeleton for policy license generation.

This script intentionally does NOT implement signing yet.
Use it to generate a normalized JSON template for later private signing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_payload(sub: str, exp: int, devices: list[str]) -> dict[str, Any]:
    return {
        "sub": sub,
        "iat": 0,
        "exp": exp,
        "devices": devices,
        "kid": "phase0-template",
        "sig": "UNSIGNED_PHASE0_TEMPLATE",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Generate policy license JSON template (unsigned).")
    p.add_argument("--sub", required=True, help="customer subject id")
    p.add_argument("--exp", type=int, default=0, help="expiry unix timestamp, 0 for perpetual")
    p.add_argument("--device", action="append", default=[], help="optional bound device id")
    p.add_argument("--out", required=True, help="output path")
    args = p.parse_args()

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(sub=args.sub, exp=int(args.exp), devices=list(args.device))
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[phase0] wrote unsigned license template: {out}")
    print("[phase0] note: signing is not implemented in this public script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

