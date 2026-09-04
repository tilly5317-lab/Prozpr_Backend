"""MF Central request/response cryptography.

MFC wraps *every* client API call in two independent layers, and gets them
subtly different, so they are implemented separately here:

**A. API envelope** (``newCasRequest``, ``validateQRCode``) — the payload is
AES-256-CBC encrypted with a key derived as ``SHA-256(shared_key)[:32]`` and a
**constant** IV MFC hands over at onboarding, then base64'd, then signed with
our RSA private key. The wire body is ``{"signature": ..., "request": ...}``;
responses come back the same shape and are verified with MFC's public key
before decryption.

**B. URL envelope** (the investor redirect) — same cipher, but the 32-byte URL
key is used **raw** (no SHA-256 pass) and the IV is random per request, carried
in the ciphertext as ``<iv_hex>:<data_hex>``.

Applying A's key derivation to B (or B's raw key to A) produces perfectly valid
base64 that MFC rejects with an opaque 422, so the two deliberately do not
share a code path.

Signature format
----------------
MFC's integration guide shows a plain ``jwt.sign({data}, key, RS256)``, but the
signatures captured in their own Postman collection are RFC 7797 **detached**
JWS: a ``{"alg":"RS256","kid":...,"b64":false}`` header, an empty payload
segment, and the signature computed over ``header.encrypted_request``. The wire
capture wins — :func:`sign_detached_jws` implements that — and
``MFC_SIGNATURE_MODE=jwt`` falls back to the guide's shape if a tenant turns
out to want it.

Pure functions, no I/O; :mod:`mfc_client` owns the HTTP.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class MfcCryptoError(Exception):
    """A key is malformed, or a payload failed to decrypt / verify."""


# --------------------------------------------------------------------------- block cipher

_BLOCK = 16


def _pkcs7_pad(data: bytes) -> bytes:
    pad = _BLOCK - (len(data) % _BLOCK)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % _BLOCK:
        raise MfcCryptoError("Decrypted payload is not a whole number of AES blocks.")
    pad = data[-1]
    if pad < 1 or pad > _BLOCK or data[-pad:] != bytes([pad]) * pad:
        raise MfcCryptoError("Decrypted payload has invalid PKCS#7 padding.")
    return data[:-pad]


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return _pkcs7_unpad(decryptor.update(ciphertext) + decryptor.finalize())


# --------------------------------------------------------------------------- A: API envelope


def derive_api_key(shared_key: str) -> bytes:
    """``SHA-256(shared_key)[:32]`` — MFC's key derivation for the API envelope.

    SHA-256 is already 32 bytes, so the documented slice is a no-op; it stays
    literal because their reference implementation writes it that way, and a
    change of hash on their side would otherwise silently change the key.
    """
    return hashlib.sha256(shared_key.encode("utf-8")).digest()[:32]


def resolve_iv(raw: str) -> bytes:
    """The constant IV MFC issues, accepted as 16 raw chars, 32 hex, or base64.

    Their samples show all three depending on which SDK the tenant was handed,
    and every one of them decodes to the same 16 bytes.
    """
    text = (raw or "").strip()
    if not text:
        raise MfcCryptoError("MFC_IV is not configured.")
    if len(text) == _BLOCK:
        return text.encode("utf-8")
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise MfcCryptoError(
            "MFC_IV must be 16 characters, 32 hex digits, or base64 of 16 bytes."
        ) from exc
    if len(decoded) != _BLOCK:
        raise MfcCryptoError(f"MFC_IV decodes to {len(decoded)} bytes, expected 16.")
    return decoded


def normalize_b64(text: str) -> str:
    """MFC's helper endpoints hand back URL-safe base64 with the padding
    stripped; the client APIs use standard base64. Accept either."""
    text = (text or "").strip().replace("-", "+").replace("_", "/")
    return text + "=" * (-len(text) % 4)


def encrypt_api_payload(payload: Any, *, shared_key: str, iv: str) -> str:
    """JSON -> AES-256-CBC -> base64, for the ``request`` field of an MFC call."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    blob = _aes_cbc_encrypt(derive_api_key(shared_key), resolve_iv(iv), body)
    return base64.b64encode(blob).decode("ascii")


def decrypt_api_payload(encrypted: str, *, shared_key: str, iv: str) -> Any:
    """The inverse, for the ``response`` field. Raises on a wrong key or IV."""
    text = (encrypted or "").strip()
    if not text:
        raise MfcCryptoError("MFC returned an empty response body.")
    try:
        blob = base64.b64decode(normalize_b64(text), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise MfcCryptoError("MFC response is not valid base64.") from exc
    plain = _aes_cbc_decrypt(derive_api_key(shared_key), resolve_iv(iv), blob)
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise MfcCryptoError(
            "MFC response decrypted but is not JSON — check the encryption key."
        ) from exc


# --------------------------------------------------------------------------- B: URL envelope


def resolve_url_key(raw: str) -> bytes:
    """The redirect key is used **raw**, so it must already be exactly 32 bytes."""
    key = (raw or "").strip().encode("utf-8")
    if len(key) != 32:
        raise MfcCryptoError(
            f"MFC_URL_ENCRYPTION_KEY must be exactly 32 characters (got {len(key)})."
        )
    return key


def encrypt_redirect_payload(payload: dict[str, Any], *, url_key: str) -> str:
    """``<iv_hex>:<ciphertext_hex>`` for the ``?data=`` parameter of the investor
    redirect. The IV is fresh per call, as MFC's UI expects."""
    iv = os.urandom(_BLOCK)
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    blob = _aes_cbc_encrypt(resolve_url_key(url_key), iv, body)
    return f"{iv.hex()}:{blob.hex()}"


def decrypt_redirect_payload(data: str, *, url_key: str) -> dict[str, Any]:
    """Inverse of :func:`encrypt_redirect_payload`. MFC's UI is the real
    consumer; this exists so the wiring can be proved locally without one."""
    if ":" not in (data or ""):
        raise MfcCryptoError("Redirect payload is not in <iv_hex>:<data_hex> form.")
    iv_hex, blob_hex = data.split(":", 1)
    try:
        iv, blob = bytes.fromhex(iv_hex), bytes.fromhex(blob_hex)
    except ValueError as exc:
        raise MfcCryptoError("Redirect payload is not valid hex.") from exc
    plain = _aes_cbc_decrypt(resolve_url_key(url_key), iv, blob)
    return json.loads(plain.decode("utf-8"))


# --------------------------------------------------------------------------- signatures


def _as_pem(raw: str, kind: str) -> bytes:
    """Accept a real PEM, a PEM with escaped newlines (the usual ``.env``
    casualty), or a bare base64 DER body, and return canonical PEM bytes."""
    text = (raw or "").strip().replace("\\n", "\n")
    if not text:
        raise MfcCryptoError(f"MFC {kind.lower()} key is not configured.")
    if "-----BEGIN" in text:
        return text.encode("utf-8")
    label = "RSA PRIVATE KEY" if kind == "PRIVATE" else "PUBLIC KEY"
    body = "".join(text.split())
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    joined = "\n".join(lines)
    return f"-----BEGIN {label}-----\n{joined}\n-----END {label}-----\n".encode("utf-8")


def _load_private_key(pem: str) -> RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(_as_pem(pem, "PRIVATE"), password=None)
    except MfcCryptoError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MfcCryptoError(f"MFC_PRIVATE_KEY could not be parsed: {exc}") from exc
    if not isinstance(key, RSAPrivateKey):
        raise MfcCryptoError("MFC_PRIVATE_KEY is not an RSA private key.")
    return key


def _load_public_key(pem: str) -> RSAPublicKey:
    try:
        key = serialization.load_pem_public_key(_as_pem(pem, "PUBLIC"))
    except MfcCryptoError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MfcCryptoError(f"MFC_PUBLIC_KEY could not be parsed: {exc}") from exc
    if not isinstance(key, RSAPublicKey):
        raise MfcCryptoError("MFC_PUBLIC_KEY is not an RSA public key.")
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def sign_detached_jws(
    encrypted_request: str, *, private_key_pem: str, kid: Optional[str] = None
) -> str:
    """RFC 7797 detached JWS: ``<header>..<signature>``.

    The payload segment is empty on the wire — MFC recomputes it from the
    ``request`` field of the same body — and the signing input is
    ``<header_b64>.<encrypted_request>`` with the ciphertext used **verbatim**,
    not re-encoded. That verbatim use is exactly what ``"b64": false`` means,
    and it is the half that a naive JWT library gets wrong.
    """
    header: dict[str, Any] = {"alg": "RS256", "b64": False, "crit": ["b64"]}
    if kid:
        header["kid"] = kid
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{encrypted_request}".encode("utf-8")
    signature = _load_private_key(private_key_pem).sign(
        signing_input, asym_padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{header_b64}..{_b64url(signature)}"


def sign_compact_jwt(encrypted_request: str, *, private_key_pem: str) -> str:
    """The integration guide's alternative: a normal RS256 JWT whose single
    claim is ``{"data": <encrypted_request>}``. Selected with
    ``MFC_SIGNATURE_MODE=jwt``."""
    import jwt  # local import: only this mode needs PyJWT

    return jwt.encode(
        {"data": encrypted_request},
        _as_pem(private_key_pem, "PRIVATE").decode("utf-8"),
        algorithm="RS256",
    )


def verify_signature(
    signature: str, encrypted_response: str, *, public_key_pem: str
) -> bool:
    """True when ``signature`` covers ``encrypted_response``.

    Tries the detached form first, then the compact-JWT form, because which one
    MFC returns is a property of their tenant configuration rather than of our
    request. A failure here is not fatal on its own — :mod:`mfc_client` decides
    whether to reject or merely log, so an unverified-but-decryptable response
    can still be diagnosed while the UAT keys are being sorted out.
    """
    sig = (signature or "").strip()
    if not sig:
        return False
    try:
        public_key = _load_public_key(public_key_pem)
    except MfcCryptoError:
        return False

    parts = sig.split(".")
    if len(parts) == 3 and parts[1] == "":
        try:
            public_key.verify(
                base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4)),
                f"{parts[0]}.{encrypted_response}".encode("utf-8"),
                asym_padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception:  # noqa: BLE001 — fall through to the compact form
            pass

    try:
        import jwt

        claims = jwt.decode(
            sig,
            _as_pem(public_key_pem, "PUBLIC").decode("utf-8"),
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_exp": False},
        )
        return str(claims.get("data") or "") == encrypted_response
    except Exception:  # noqa: BLE001
        return False
