"""
Encryption-at-rest for secret settings (API keys) using Windows DPAPI.

DPAPI (CryptProtectData) encrypts with a key derived from the Windows user
account, so no key material is stored anywhere in this project. Values are
stored as "dpapi:<base64>"; anything without that prefix is passed through
unchanged, which keeps pre-existing plaintext keys working (they are
re-encrypted the next time they are saved).

Honest limitation (documented in the changelog): DPAPI is user-scoped. If
the database file is opened under a different Windows account (or a profile
whose DPAPI master keys did not roam), encrypted values cannot be decrypted;
they read back as empty and must be re-entered in Settings. On non-Windows
platforms the store degrades to plaintext with a logged warning.
"""

import base64
import ctypes
import ctypes.wintypes
import logging
import sys

log = logging.getLogger("crypto_investigator.secretstore")

SECRET_PREFIX = "dpapi:"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_bytes(blob: "_DataBlob") -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return data


def _dpapi(raw: bytes, encrypt: bool) -> bytes:
    blob_in = _DataBlob(len(raw), ctypes.cast(
        ctypes.create_string_buffer(raw, len(raw)),
        ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    func = (ctypes.windll.crypt32.CryptProtectData if encrypt
            else ctypes.windll.crypt32.CryptUnprotectData)
    ok = func(ctypes.byref(blob_in), None, None, None, None,
              _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI call failed")
    return _blob_bytes(blob_out)


def protect(plain: str) -> str:
    """Encrypt a secret for storage. Empty stays empty (a cleared key)."""
    if not plain:
        return ""
    if sys.platform != "win32":
        log.warning("DPAPI unavailable on this platform; storing setting "
                    "as plaintext.")
        return plain
    encrypted = _dpapi(plain.encode("utf-8"), encrypt=True)
    return SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(stored: str) -> str:
    """Decrypt a stored secret. Plaintext (legacy) values pass through;
    an undecryptable value reads back as empty rather than as garbage."""
    if not stored or not stored.startswith(SECRET_PREFIX):
        return stored
    try:
        raw = base64.b64decode(stored[len(SECRET_PREFIX):])
        return _dpapi(raw, encrypt=False).decode("utf-8")
    except Exception:
        log.warning("could not decrypt a stored API key (different "
                    "Windows account/profile?). Re-enter it in "
                    "Settings.")
        return ""
