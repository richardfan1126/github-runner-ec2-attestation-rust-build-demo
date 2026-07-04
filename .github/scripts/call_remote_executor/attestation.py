"""Attestation validation functions for NitroTPM COSE Sign1 documents."""

import base64
import hashlib
import json
import logging

import cbor2
from pycose.messages import Sign1Message
from pycose.keys import EC2Key
from pycose.headers import Algorithm, KID
from pycose.algorithms import Es384
from pycose.keys.keyparam import EC2KpCurve, EC2KpX, EC2KpY
from pycose.keys.curves import P384
from OpenSSL import crypto as ossl_crypto
from Crypto.Util.number import long_to_bytes
from cryptography.x509 import load_der_x509_certificate

from .errors import CallerError

logger = logging.getLogger(__name__)

# Maximum accepted size for base64-encoded attestation documents before decoding.
# Prevents resource exhaustion from oversized server-supplied blobs (Req 4A.8).
MAX_ATTESTATION_B64_SIZE = 1_000_000  # 1 MB

# Claims-digest binding version pins. Mirror the server's ENVELOPE_VERSION and
# CLAIMS_SCHEMA_VERSION constants (attestation-claims-digest). Scalar, not a
# set/range: a genuine format transition is a deliberate one-line bump, not a
# membership tweak — no dual-format support (design D5, resolved OQ).
ACCEPTED_ENVELOPE_VERSION = 1
ACCEPTED_CLAIMS_SCHEMA_MAJOR = 1
SHA256_DIGEST_PREFIX = "sha256:"

EXPECTED_ATTESTATION_FIELDS = [
    "module_id",
    "digest",
    "timestamp",
    "nitrotpm_pcrs",
    "certificate",
    "cabundle",
]


def decode_cose_sign1(raw_bytes: bytes, phase: str) -> list:
    """Decode raw bytes into a COSE_Sign1 4-element array.

    Handles both CBOR-tagged (tag 18) and untagged representations.
    Returns the 4-element array [protected, unprotected, payload, signature].
    Raises CallerError on decode/structure failures.
    """
    try:
        decoded = cbor2.loads(raw_bytes)
    except Exception as exc:
        raise CallerError(
            message=f"Failed to CBOR-decode document: {exc}",
            phase=phase,
            details={"error": str(exc)},
        )

    # Unwrap CBOR tag 18 (COSE_Sign1) if present
    if isinstance(decoded, cbor2.CBORTag):
        if decoded.tag != 18:
            raise CallerError(
                message=f"Unexpected CBOR tag {decoded.tag}, expected 18 (COSE_Sign1)",
                phase=phase,
                details={"tag": decoded.tag},
            )
        cose_array = decoded.value
    else:
        cose_array = decoded

    if not isinstance(cose_array, (list, tuple)) or len(cose_array) != 4:
        raise CallerError(
            message="CBOR result is not a valid COSE_Sign1 structure (expected 4-element array)",
            phase=phase,
            details={
                "type": type(cose_array).__name__,
                "length": len(cose_array) if isinstance(cose_array, (list, tuple)) else None,
            },
        )

    return list(cose_array)


def verify_certificate_chain(cert_der: bytes, cabundle: list[bytes], root_cert_pem: str) -> None:
    """Validate the signing certificate against the CA bundle and root certificate.

    Per AWS docs, cabundle is ordered [ROOT_CERT, INTERM_1, INTERM_2, ..., INTERM_N].
    The chain for validation is: TARGET_CERT <- INTERM_N <- ... <- INTERM_1 <- ROOT_CERT.
    Raises CallerError if certificate chain validation fails.
    """
    if not root_cert_pem:
        return

    try:
        store = ossl_crypto.X509Store()
        # ONLY the pinned root certificate is a trust anchor
        store.add_cert(ossl_crypto.load_certificate(ossl_crypto.FILETYPE_PEM, root_cert_pem))

        # All cabundle entries are passed as UNTRUSTED intermediates —
        # they are NOT added to the trust store, preventing a malicious server
        # from injecting its own CA into the cabundle to forge attestations.
        untrusted_intermediates = [
            ossl_crypto.load_certificate(ossl_crypto.FILETYPE_ASN1, der_cert)
            for der_cert in cabundle
        ]

        signing_cert = ossl_crypto.load_certificate(ossl_crypto.FILETYPE_ASN1, cert_der)
        store_ctx = ossl_crypto.X509StoreContext(store, signing_cert, chain=untrusted_intermediates)
        store_ctx.verify_certificate()
    except Exception as exc:
        if isinstance(exc, CallerError):
            raise
        raise CallerError(
            message=f"Certificate chain validation failed: {exc}",
            phase="attestation",
            details={"error": str(exc)},
        )


def verify_cose_signature(cose_array: list, root_cert_pem: str) -> None:
    """Verify the COSE_Sign1 signature using the signing certificate's public key.

    The COSE_Sign1 structure per AWS docs:
      [protected_header, unprotected_header, payload, signature]
    where protected_header = {1: -35} (algorithm: ECDSA 384).
    Raises CallerError if signature verification fails.
    """
    if not root_cert_pem:
        return

    try:
        payload_doc = cbor2.loads(cose_array[2])
        cert_der = payload_doc["certificate"]

        cert = load_der_x509_certificate(cert_der)
        pub_numbers = cert.public_key().public_numbers()

        x_bytes = long_to_bytes(pub_numbers.x)
        y_bytes = long_to_bytes(pub_numbers.y)

        # Pad to 48 bytes (P-384 coordinate size)
        x_bytes = x_bytes.rjust(48, b'\x00')
        y_bytes = y_bytes.rjust(48, b'\x00')

        cose_key = EC2Key.from_dict({
            EC2KpCurve: P384,
            EC2KpX: x_bytes,
            EC2KpY: y_bytes,
        })

        # Decode protected header — it's CBOR-encoded bytes in the array
        phdr = cbor2.loads(cose_array[0]) if isinstance(cose_array[0], bytes) else cose_array[0]
        uhdr = cose_array[1] if cose_array[1] else {}

        msg = Sign1Message(
            phdr=phdr,
            uhdr=uhdr,
            payload=cose_array[2],
        )
        msg.signature = cose_array[3]
        msg.key = cose_key

        if not msg.verify_signature():
            raise CallerError(
                message="COSE Sign1 signature verification failed",
                phase="attestation",
            )
    except CallerError:
        raise
    except Exception as exc:
        raise CallerError(
            message=f"COSE signature verification error: {exc}",
            phase="attestation",
            details={"error": str(exc)},
        )


def validate_pcrs(document_pcrs: dict, expected_pcrs: dict[int, str] | None) -> None:
    """Compare expected PCR values against those in the attestation document.

    Raises CallerError if any expected PCR is missing or mismatched.
    """
    if not expected_pcrs:
        return

    for index, expected_hex in expected_pcrs.items():
        idx = int(index)
        if idx not in document_pcrs or document_pcrs[idx] is None:
            raise CallerError(
                message=f"PCR index {idx} not found in attestation document",
                phase="attestation",
                details={"missing_pcr_index": idx},
            )
        actual_hex = document_pcrs[idx].hex()
        if actual_hex != expected_hex:
            raise CallerError(
                message=f"PCR {idx} mismatch: expected {expected_hex}, got {actual_hex}",
                phase="attestation",
                details={"pcr_index": idx, "expected": expected_hex, "actual": actual_hex},
            )


def verify_nonce(payload_doc: dict, expected_nonce: str, phase: str) -> None:
    """Verify the nonce field in the attestation payload matches the expected nonce.

    Raises CallerError if the nonce is missing or does not match.
    """
    nonce_raw = payload_doc.get("nonce")
    if nonce_raw is None:
        raise CallerError(
            message="Attestation document missing nonce field",
            phase=phase,
            details={"expected_nonce": expected_nonce},
        )
    if isinstance(nonce_raw, bytes):
        nonce_value = nonce_raw.decode("utf-8")
    else:
        nonce_value = str(nonce_raw)
    if nonce_value != expected_nonce:
        raise CallerError(
            message=f"Nonce mismatch: expected {expected_nonce}, got {nonce_value}",
            phase=phase,
            details={"expected": expected_nonce, "actual": nonce_value},
        )


def _verify_claims_binding(payload_doc: dict, claims_raw: str | None, phase: str) -> dict:
    """Verify the integrity binding of claims_raw against the signed envelope digest.

    payload_doc MUST already be COSE-verified (design D2: this runs strictly
    downstream of signature/PKI/PCR/nonce checks). Reads the {v, claims_digest,
    timestamp, execution_id} envelope from the trusted user_data, rejects an
    unknown envelope version, base64-decodes claims_raw, verifies
    sha256(decode(claims_raw)) == claims_digest, parses the claims JSON, and
    rejects an unknown schema_version MAJOR.

    Does NOT check which claim fields are present within claims — presence is
    per-phase (design D4/D6) and lives in the phase-specific validators.

    Returns the parsed claims dict. Raises CallerError (fail closed) on any
    missing preimage, decode failure, digest mismatch, or version rejection.
    """
    if not claims_raw:
        raise CallerError(
            message=(
                f"claims_raw is missing for {phase}: cannot verify the claims-digest "
                f"integrity binding (absence is treated as tampering, not optionality)"
            ),
            phase=phase,
        )

    user_data_raw = payload_doc.get("user_data")
    if user_data_raw is None:
        raise CallerError(
            message=f"Attestation document missing user_data envelope for {phase}",
            phase=phase,
        )
    user_data_str = (
        user_data_raw.decode("utf-8") if isinstance(user_data_raw, bytes) else str(user_data_raw)
    )
    try:
        envelope = json.loads(user_data_str)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CallerError(
            message=f"Failed to parse user_data envelope for {phase}: {exc}",
            phase=phase,
            details={"user_data": user_data_str, "error": str(exc)},
        )

    envelope_version = envelope.get("v")
    if envelope_version != ACCEPTED_ENVELOPE_VERSION:
        raise CallerError(
            message=(
                f"Unknown envelope version for {phase}: expected "
                f"{ACCEPTED_ENVELOPE_VERSION}, got {envelope_version!r}"
            ),
            phase=phase,
            details={"expected": ACCEPTED_ENVELOPE_VERSION, "actual": envelope_version},
        )

    claims_digest = envelope.get("claims_digest")
    if not isinstance(claims_digest, str) or not claims_digest.startswith(SHA256_DIGEST_PREFIX):
        raise CallerError(
            message=(
                f"claims_digest missing or uses an unsupported algorithm prefix for "
                f"{phase}: {claims_digest!r}"
            ),
            phase=phase,
            details={"claims_digest": claims_digest},
        )

    try:
        decoded_claims = base64.b64decode(claims_raw, validate=True)
    except Exception as exc:
        raise CallerError(
            message=f"Failed to base64-decode claims_raw for {phase}: {exc}",
            phase=phase,
            details={"error": str(exc)},
        )

    computed_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(decoded_claims).hexdigest()
    if computed_digest != claims_digest:
        raise CallerError(
            message=f"Claims integrity binding failed for {phase}: digest mismatch",
            phase=phase,
            details={"computed": computed_digest, "attested": claims_digest},
        )

    try:
        claims = json.loads(decoded_claims)
    except json.JSONDecodeError as exc:
        raise CallerError(
            message=f"Failed to parse claims JSON for {phase}: {exc}",
            phase=phase,
            details={"error": str(exc)},
        )
    if not isinstance(claims, dict):
        raise CallerError(
            message=f"Claims document for {phase} is not a JSON object, got {type(claims).__name__}",
            phase=phase,
            details={"type": type(claims).__name__},
        )

    schema_version = claims.get("schema_version")
    if not isinstance(schema_version, str) or "." not in schema_version:
        raise CallerError(
            message=f"Claims schema_version missing or malformed for {phase}: {schema_version!r}",
            phase=phase,
            details={"schema_version": schema_version},
        )
    try:
        major = int(schema_version.split(".", 1)[0])
    except ValueError:
        raise CallerError(
            message=(
                f"Claims schema_version MAJOR is not an integer for {phase}: "
                f"{schema_version!r}"
            ),
            phase=phase,
            details={"schema_version": schema_version},
        )
    if major != ACCEPTED_CLAIMS_SCHEMA_MAJOR:
        raise CallerError(
            message=(
                f"Unknown claims schema_version MAJOR for {phase}: expected "
                f"{ACCEPTED_CLAIMS_SCHEMA_MAJOR}, got {major} (schema_version={schema_version!r})"
            ),
            phase=phase,
            details={"expected_major": ACCEPTED_CLAIMS_SCHEMA_MAJOR, "schema_version": schema_version},
        )

    return claims


def validate_attestation(
    attestation_b64: str,
    root_cert_pem: str,
    expected_pcrs: dict[int, str] | None,
    expected_nonce: str | None = None,
) -> dict:
    """Decode base64 -> CBOR -> COSE Sign1 array. Validate and verify.

    Returns parsed attestation payload dict.
    Raises CallerError on decode/parse/validation/verification failures.
    """
    # Enforce maximum size before decoding to prevent resource exhaustion (Req 4A.8)
    if len(attestation_b64) > MAX_ATTESTATION_B64_SIZE:
        raise CallerError(
            message=(
                f"Attestation document exceeds maximum allowed size "
                f"({len(attestation_b64)} > {MAX_ATTESTATION_B64_SIZE} bytes)"
            ),
            phase="attestation",
            details={
                "size": len(attestation_b64),
                "max_size": MAX_ATTESTATION_B64_SIZE,
            },
        )

    # Base64-decode the attestation string to binary
    try:
        raw_bytes = base64.b64decode(attestation_b64)
    except Exception as exc:
        raise CallerError(
            message=f"Failed to base64-decode attestation document: {exc}",
            phase="attestation",
            details={"error": str(exc)},
        )

    # CBOR-decode the binary — expect a COSE_Sign1 structure (tag 18)
    cose_array = decode_cose_sign1(raw_bytes, phase="attestation")

    # CBOR-decode the payload (index 2) to extract attestation fields
    try:
        payload_doc = cbor2.loads(cose_array[2])
    except Exception as exc:
        raise CallerError(
            message=f"Failed to CBOR-decode attestation payload: {exc}",
            phase="attestation",
            details={"error": str(exc)},
        )

    if not isinstance(payload_doc, dict):
        raise CallerError(
            message=f"Attestation payload is not a map, got {type(payload_doc).__name__}",
            phase="attestation",
            details={"type": type(payload_doc).__name__},
        )

    # Verify all expected structural fields are present
    missing = [f for f in EXPECTED_ATTESTATION_FIELDS if f not in payload_doc]
    if missing:
        raise CallerError(
            message=f"Attestation document missing fields: {missing}",
            phase="attestation",
            details={"missing_fields": missing},
        )

    # Validate certificate chain (PKI)
    verify_certificate_chain(payload_doc["certificate"], payload_doc["cabundle"], root_cert_pem)

    # Verify COSE Sign1 signature
    verify_cose_signature(cose_array, root_cert_pem)

    # Validate PCR values
    validate_pcrs(payload_doc["nitrotpm_pcrs"], expected_pcrs)

    # Verify nonce freshness if expected
    if expected_nonce is not None:
        verify_nonce(payload_doc, expected_nonce, phase="attestation")

    # Log attestation document fields for audit
    for field in EXPECTED_ATTESTATION_FIELDS:
        if field in ("certificate", "cabundle"):
            continue
        if field == "nitrotpm_pcrs":
            hex_pcrs = {
                idx: val.hex() if isinstance(val, bytes) else val
                for idx, val in payload_doc[field].items()
            }
            logger.info("Attestation field %s: %s", field, hex_pcrs)
        else:
            logger.info("Attestation field %s: %s", field, payload_doc[field])
    for field in ("user_data", "nonce"):
        if field in payload_doc and payload_doc[field] is not None:
            val = payload_doc[field]
            decoded = val.decode() if isinstance(val, bytes) else val
            logger.info("Attestation field %s: %s", field, decoded)

    return payload_doc


# Claim fields the execution-acceptance phase requires within the accepted claims
# schema_version MAJOR (design D4/D6). Every one is either sent by the caller or
# independently recomputed, so an absent field is tampering, not legitimate
# evolution — the version gate in _verify_claims_binding is what makes that safe.
EXECUTION_CLAIMS_REQUIRED_FIELDS = (
    "repository_url",
    "commit_hash",
    "script_path",
    "script_env_hash",
)


def validate_execution_attestation(
    attestation_b64: str,
    claims_raw: str | None,
    root_cert_pem: str,
    expected_pcrs: dict[int, str] | None,
    expected_nonce: str | None = None,
) -> tuple[dict, dict]:
    """Validate the execution-acceptance attestation and its claims-digest binding.

    Composes the existing bare COSE verifier (validate_attestation — no new or
    duplicated COSE code, design D3) with the claims integrity binding and
    version gate, then enforces the execution-phase mandatory field set
    (design D4/D6). Used by /execute.

    Returns (payload_doc, claims) — the trusted attestation payload and the
    verified claims dict, from which request-binding fields must be read
    (never from raw user_data).
    Raises CallerError on any COSE, binding, version, or presence failure.
    """
    payload_doc = validate_attestation(attestation_b64, root_cert_pem, expected_pcrs, expected_nonce)
    claims = _verify_claims_binding(payload_doc, claims_raw, phase="execute")

    missing = [f for f in EXECUTION_CLAIMS_REQUIRED_FIELDS if f not in claims]
    if missing:
        raise CallerError(
            message=f"Execution claims missing required fields: {missing}",
            phase="execute",
            details={"missing_fields": missing},
        )

    return payload_doc, claims


def validate_output_attestation(
    output_attestation_b64: str,
    claims_raw: str | None,
    stdout: str,
    stderr: str,
    exit_code: int,
    root_cert_pem: str,
    expected_pcrs: dict[int, str] | None,
    expected_nonce: str | None = None,
) -> bool:
    """Decode output attestation CBOR, verify the claims-digest binding, check output_digest.

    Keeps its own inline COSE steps (pre-existing duplication with
    validate_attestation, left as-is — Non-Goal). After COSE verification,
    runs the shared claims integrity binding + version gate
    (_verify_claims_binding) and requires only output_digest from the output
    claims (design D6 — the four execution fields are legitimately absent here
    and MUST NOT be required). Recomputes output_digest over the canonical
    JSON object {"stdout", "stderr", "exit_code"} (sort_keys, compact
    separators, sha256:-prefixed) and compares to the attested value.
    Returns True if match.
    Raises CallerError on decode/parse/binding/version failures or digest mismatch.
    """
    # Enforce maximum size before decoding to prevent resource exhaustion (Req 4A.8)
    if len(output_attestation_b64) > MAX_ATTESTATION_B64_SIZE:
        raise CallerError(
            message=(
                f"Output attestation document exceeds maximum allowed size "
                f"({len(output_attestation_b64)} > {MAX_ATTESTATION_B64_SIZE} bytes)"
            ),
            phase="output_attestation",
            details={
                "size": len(output_attestation_b64),
                "max_size": MAX_ATTESTATION_B64_SIZE,
            },
        )

    # Decode base64 → CBOR → COSE_Sign1 (tag 18) 4-element array
    try:
        raw_bytes = base64.b64decode(output_attestation_b64)
    except Exception as exc:
        raise CallerError(
            message=f"Failed to base64-decode output attestation document: {exc}",
            phase="output_attestation",
            details={"error": str(exc)},
        )

    cose_array = decode_cose_sign1(raw_bytes, phase="output_attestation")

    # CBOR-decode payload to extract attestation fields
    try:
        payload_doc = cbor2.loads(cose_array[2])
    except Exception as exc:
        raise CallerError(
            message=f"Failed to CBOR-decode output attestation payload: {exc}",
            phase="output_attestation",
            details={"error": str(exc)},
        )

    if not isinstance(payload_doc, dict):
        raise CallerError(
            message=f"Output attestation payload is not a map, got {type(payload_doc).__name__}",
            phase="output_attestation",
            details={"type": type(payload_doc).__name__},
        )

    # Validate structural fields
    missing = [f for f in EXPECTED_ATTESTATION_FIELDS if f not in payload_doc]
    if missing:
        raise CallerError(
            message=f"Output attestation document missing fields: {missing}",
            phase="output_attestation",
            details={"missing_fields": missing},
        )

    # Validate certificate chain (PKI) against root cert
    try:
        verify_certificate_chain(payload_doc["certificate"], payload_doc["cabundle"], root_cert_pem)
    except CallerError as exc:
        raise CallerError(
            message=exc.message,
            phase="output_attestation",
            details=exc.details,
        )

    # Verify COSE Sign1 signature
    try:
        verify_cose_signature(cose_array, root_cert_pem)
    except CallerError as exc:
        raise CallerError(
            message=exc.message,
            phase="output_attestation",
            details=exc.details,
        )

    # Validate PCR values
    try:
        validate_pcrs(payload_doc["nitrotpm_pcrs"], expected_pcrs)
    except CallerError as exc:
        raise CallerError(
            message=exc.message,
            phase="output_attestation",
            details=exc.details,
        )

    # Verify nonce freshness if expected
    if expected_nonce is not None:
        try:
            verify_nonce(payload_doc, expected_nonce, phase="output_attestation")
        except CallerError as exc:
            raise CallerError(
                message=exc.message,
                phase="output_attestation",
                details=exc.details,
            )

    # Log attestation document fields for audit
    for field in EXPECTED_ATTESTATION_FIELDS:
        if field in ("certificate", "cabundle"):
            continue
        if field == "nitrotpm_pcrs":
            hex_pcrs = {
                idx: val.hex() if isinstance(val, bytes) else val
                for idx, val in payload_doc[field].items()
            }
            logger.info("Attestation field %s: %s", field, hex_pcrs)
        else:
            logger.info("Attestation field %s: %s", field, payload_doc[field])
    for field in ("user_data", "nonce"):
        if field in payload_doc and payload_doc[field] is not None:
            val = payload_doc[field]
            decoded = val.decode() if isinstance(val, bytes) else val
            logger.info("Attestation field %s: %s", field, decoded)

    # Claims-digest integrity binding + version gate (design D2/D5), then the
    # output-phase mandatory set: only output_digest is required (design D6) —
    # the four execution fields are legitimately absent from output claims.
    claims = _verify_claims_binding(payload_doc, claims_raw, phase="output_attestation")

    if "output_digest" not in claims:
        raise CallerError(
            message="Output claims missing required field: output_digest",
            phase="output_attestation",
        )
    attestation_digest = claims["output_digest"]

    # Recompute over the canonical JSON object (design D7) — replaces the
    # retired delimiter-glued stdout:...\nstderr:...\nexit_code:... form.
    canonical_output = json.dumps(
        {"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
        sort_keys=True,
        separators=(",", ":"),
    )
    computed_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(canonical_output.encode("utf-8")).hexdigest()

    if computed_digest != attestation_digest:
        raise CallerError(
            message="Output integrity verification failed: digest mismatch",
            phase="output_attestation",
            details={"computed": computed_digest, "attestation": attestation_digest},
        )

    logger.info("Output integrity verification succeeded")
    return True
