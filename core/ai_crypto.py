"""Encrypt/decrypt Groq API keys at rest using app secret."""
from __future__ import annotations
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret: str) -> Fernet:
    dig = hashlib.sha256((secret or "estacle").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(dig))


def encrypt_secret(plain: str, app_secret: str) -> str:
    if not plain:
        return ""
    return _fernet(app_secret).encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str, app_secret: str) -> str:
    if not token:
        return ""
    try:
        return _fernet(app_secret).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""


def mask_key(plain: str) -> str:
    if not plain:
        return ""
    if len(plain) <= 8:
        return "••••••••"
    return plain[:4] + "••••" + plain[-4:]
