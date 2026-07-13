import os
import json
from cryptography.fernet import Fernet, InvalidToken
import logging

class EncryptionUnavailableError(Exception):
    pass

class EncryptionConfigurationError(Exception):
    pass

class DecryptionError(Exception):
    pass

class UnsupportedEncryptionVersionError(Exception):
    pass


def _get_key_for_version(version: int) -> bytes:
    key_b64 = os.environ.get(f"DATA_ENCRYPTION_KEY_V{version}")
    if not key_b64:
        raise UnsupportedEncryptionVersionError(f"No key configured for version {version}")
    try:
        return key_b64.encode('utf-8')
    except Exception as e:
        raise EncryptionConfigurationError(f"Invalid key format for version {version}") from e


def is_configured() -> bool:
    try:
        active = os.environ.get("DATA_ENCRYPTION_ACTIVE_VERSION")
        if not active:
            return False
        version = int(active)
        key = os.environ.get(f"DATA_ENCRYPTION_KEY_V{version}")
        return bool(key)
    except ValueError:
        return False


def get_active_version() -> int:
    if not is_configured():
        raise EncryptionUnavailableError("Encryption is not configured")
    try:
        return int(os.environ["DATA_ENCRYPTION_ACTIVE_VERSION"])
    except (KeyError, ValueError) as e:
        raise EncryptionConfigurationError("DATA_ENCRYPTION_ACTIVE_VERSION is missing or invalid") from e


def encrypt_text(value: str) -> dict:
    if not value:
        return {"ciphertext": "", "version": get_active_version()}
        
    version = get_active_version()
    key = _get_key_for_version(version)
    
    try:
        fernet = Fernet(key)
        ciphertext = fernet.encrypt(value.encode('utf-8')).decode('utf-8')
        return {"ciphertext": ciphertext, "version": version}
    except Exception as e:
        logging.error("Failed to encrypt text safely")
        raise EncryptionUnavailableError("Failed to encrypt text") from e


def decrypt_text(ciphertext: str, version: int) -> str:
    if not ciphertext:
        return ""
        
    try:
        key = _get_key_for_version(version)
        fernet = Fernet(key)
        return fernet.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except InvalidToken as e:
        logging.error("Invalid token during decryption")
        raise DecryptionError("Failed to decrypt text") from e
    except Exception as e:
        logging.error("Decryption error occurred")
        raise DecryptionError("Failed to decrypt text") from e


def encrypt_json(payload: dict) -> dict:
    if not payload:
        return {"ciphertext": "", "version": get_active_version()}
        
    json_str = json.dumps(payload)
    return encrypt_text(json_str)


def decrypt_json(ciphertext: str, version: int) -> dict:
    if not ciphertext:
        return {}
        
    text = decrypt_text(ciphertext, version)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logging.error("Failed to decode decrypted JSON")
        raise DecryptionError("Invalid JSON after decryption") from e
