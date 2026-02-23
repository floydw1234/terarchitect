"""
Encrypt/decrypt app setting values using TERARCHITECT_SECRET_KEY (64-char hex, 32 bytes for AES-256).
"""
import os
import base64
from typing import Optional

def _get_secret_key_bytes() -> Optional[bytes]:
    raw = (os.environ.get("TERARCHITECT_SECRET_KEY") or "").strip()
    # Require exactly 64 hex chars (32 bytes). Strip quotes if user put key in quotes in .env
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1].strip()
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1].strip()
    if not raw or len(raw) != 64:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return None


def encrypt_value(plaintext: str) -> Optional[str]:
    """Encrypt plaintext for storage. Returns None if secret key is missing or invalid."""
    import sys
    key = _get_secret_key_bytes()
    if not key:
        raw = (os.environ.get("TERARCHITECT_SECRET_KEY") or "").strip()
        print(f"[DEBUG] encrypt_value: no key. len(TERARCHITECT_SECRET_KEY)={len(raw)}", file=sys.stderr, flush=True)
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import secrets
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        data = plaintext.encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, data, None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")
    except Exception as e:
        print(f"[DEBUG] encrypt_value exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return None


def decrypt_value(encrypted_b64: str) -> Optional[str]:
    """Decrypt a value stored by encrypt_value. Returns None if key missing/invalid or decrypt fails."""
    key = _get_secret_key_bytes()
    if not key:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(encrypted_b64.encode("ascii"))
        if len(raw) < 13:
            return None
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception:
        return None


def is_encryption_available() -> bool:
    return _get_secret_key_bytes() is not None
