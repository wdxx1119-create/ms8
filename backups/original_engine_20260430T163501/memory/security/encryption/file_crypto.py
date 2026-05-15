from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"OCMENC1\n"


class FileCryptoError(Exception):
    """Raised when encrypted payload cannot be parsed or decrypted."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def is_encrypted_blob(blob: bytes) -> bool:
    return bool(blob.startswith(MAGIC))


def encrypt_bytes(data: bytes, file_type: str, dek: bytes, kdf: str = "argon2id") -> bytes:
    nonce = os.urandom(12)
    aad = (file_type or "generic").encode("utf-8")
    cipher = AESGCM(dek)
    ciphertext = cipher.encrypt(nonce, data, aad)
    header = {
        "version": "v1",
        "cipher": "AES-256-GCM",
        "kdf": kdf,
        "nonce": _b64(nonce),
        "file_type": str(file_type or "generic"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return MAGIC + json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n" + ciphertext


def decrypt_bytes(blob: bytes, dek: bytes) -> bytes:
    if not is_encrypted_blob(blob):
        raise FileCryptoError("payload_not_encrypted")
    payload = blob[len(MAGIC) :]
    try:
        header_line, ciphertext = payload.split(b"\n", 1)
        header = json.loads(header_line.decode("utf-8"))
        nonce = _unb64(str(header["nonce"]))
        file_type = str(header.get("file_type", "generic"))
    except Exception as exc:
        raise FileCryptoError(f"invalid_encrypted_payload:{exc}") from exc
    try:
        cipher = AESGCM(dek)
        return cipher.decrypt(nonce, ciphertext, file_type.encode("utf-8"))
    except Exception as exc:
        raise FileCryptoError(f"decrypt_failed:{exc}") from exc
