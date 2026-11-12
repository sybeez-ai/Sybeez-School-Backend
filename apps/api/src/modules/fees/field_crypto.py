"""Encrypt settlement account numbers at rest.

FEE_KMS_KEY_ID uses AWS KMS. Otherwise FEE_FIELD_KEY or RAZORPAY_FIELD_KEY
is stretched into a Fernet key. The API stores only the ciphertext plus the
last four digits.
"""

from __future__ import annotations

import base64
import hashlib

from src.core.exceptions import AppError
from src.modules.fees.razorpay_secrets import razorpay_env


class FieldCryptoError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500)


def mask_account_number(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        raise FieldCryptoError("Bank account number is too short to store")
    return digits[-4:]


def _fernet():
    from cryptography.fernet import Fernet

    secret = razorpay_env("FEE_FIELD_KEY") or razorpay_env("RAZORPAY_FIELD_KEY")
    if not secret:
        raise FieldCryptoError("Bank account encryption key is not configured")
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _kms_encrypt(key_id: str, plaintext: bytes) -> bytes:
    import boto3

    from src.core.config import get_settings

    client = boto3.client("kms", region_name=get_settings().aws_region or None)
    return client.encrypt(KeyId=key_id, Plaintext=plaintext)["CiphertextBlob"]


def _kms_decrypt(blob: bytes) -> bytes:
    import boto3

    from src.core.config import get_settings

    client = boto3.client("kms", region_name=get_settings().aws_region or None)
    return client.decrypt(CiphertextBlob=blob)["Plaintext"]


def encrypt_account_number(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        raise FieldCryptoError("Bank account number is too short to store")
    kms_id = razorpay_env("FEE_KMS_KEY_ID")
    if kms_id:
        blob = _kms_encrypt(kms_id, digits.encode())
        return "kms:" + base64.b64encode(blob).decode()
    token = _fernet().encrypt(digits.encode()).decode()
    return "fernet:" + token


def decrypt_account_number(ciphertext: str) -> str:
    if ciphertext.startswith("kms:"):
        blob = base64.b64decode(ciphertext[4:])
        return _kms_decrypt(blob).decode()
    token = ciphertext[7:] if ciphertext.startswith("fernet:") else ciphertext
    return _fernet().decrypt(token.encode()).decode()
