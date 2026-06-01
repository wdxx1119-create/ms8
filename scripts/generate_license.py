#!/usr/bin/env python3
"""Generate signed policy license JSON (Ed25519)."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def build_payload(sub: str, exp: int, devices: list[str], iat: int, kid: str) -> dict[str, Any]:
    return {
        "sub": sub,
        "iat": iat,
        "exp": exp,
        "devices": devices,
        "kid": kid,
    }


def canonical_signing_bytes(payload: dict[str, Any]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Generate signed policy license JSON.")
    p.add_argument("--sub", required=True, help="customer subject id")
    p.add_argument("--exp", type=int, default=0, help="expiry unix timestamp, 0 for perpetual")
    p.add_argument("--device", action="append", default=[], help="optional bound device id")
    p.add_argument("--iat", type=int, default=0, help="issued-at unix timestamp, default now")
    p.add_argument("--kid", default="default", help="key id")
    p.add_argument("--private-key-pem", required=True, help="path to Ed25519 private key (PEM)")
    p.add_argument("--out", required=True, help="output path")
    args = p.parse_args()

    out = Path(args.out).expanduser()
    key_path = Path(args.private_key_pem).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    import time

    iat = int(args.iat) if int(args.iat) > 0 else int(time.time())
    payload = build_payload(sub=args.sub, exp=int(args.exp), devices=list(args.device), iat=iat, kid=str(args.kid))
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise SystemExit("private key must be Ed25519")
    sig = private.sign(canonical_signing_bytes(payload))
    payload["sig"] = base64.b64encode(sig).decode("utf-8")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote signed license: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
