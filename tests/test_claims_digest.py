"""Unit tests for the claims-digest integrity binding and version gate.

Validates: adopt-claims-digest-verification change — the two-layer binding
model (integrity vs request), fail-closed contract, version-gate policy, and
the /attest carve-out.

Test seam (design D3 / Thread F): the binding layer consumes an
already-trusted payload_doc (what validate_attestation returns post-COSE), so
it needs no signing key and no test CA. Presence/request-binding tests patch
validate_attestation to return a hand-built fixture; integrity/version-gate
tests call _verify_claims_binding directly as a unit.
"""

import base64
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import cbor2
import pytest

# Ensure the caller module is importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from call_remote_executor import attestation
from call_remote_executor.attestation import SHA256_DIGEST_PREFIX
from call_remote_executor.caller import RemoteExecutorCaller
from call_remote_executor.errors import CallerError

ROOT_CERT_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"
EXPECTED_PCRS = {0: "0" * 96}


def _make_claims_fixture(claims: dict, *, v=1, execution_id="exec-fixture") -> tuple[dict, str]:
    """Build a trusted payload_doc (as validate_attestation would return post-COSE)
    plus a re-bound claims_raw: real JSON claims -> base64 -> claims_digest written
    into the envelope. Integrity MUST pass so presence/binding tests exercise the
    right layer (tasks.md 6.1)."""
    claims_json = json.dumps(claims).encode("utf-8")
    claims_raw = base64.b64encode(claims_json).decode("ascii")
    claims_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(claims_json).hexdigest()
    envelope = {
        "v": v,
        "claims_digest": claims_digest,
        "timestamp": "2026-01-01T00:00:00Z",
        "execution_id": execution_id,
    }
    payload_doc = {"user_data": json.dumps(envelope).encode("utf-8")}
    return payload_doc, claims_raw


def _build_fake_attestation_b64(payload_doc: dict) -> str:
    """Build a structurally-valid (but unsigned) COSE_Sign1 CBOR blob.

    Used only with root_cert_pem="" / expected_pcrs=None, which make the
    certificate-chain and signature checks no-ops (they already short-circuit
    on falsy configuration) — this is the seam validate_output_attestation's
    inline COSE requires, since (unlike validate_execution_attestation) it does
    not compose the bare validate_attestation function.
    """
    cose_array = [b"", {}, cbor2.dumps(payload_doc), b""]
    tagged = cbor2.CBORTag(18, cose_array)
    return base64.b64encode(cbor2.dumps(tagged)).decode("ascii")


def _make_caller(**kwargs):
    defaults = {
        "server_url": "https://test.example.com",
        "root_cert_pem": ROOT_CERT_PEM,
        "expected_pcrs": EXPECTED_PCRS,
        "timeout": 5,
    }
    defaults.update(kwargs)
    return RemoteExecutorCaller(**defaults)


def _setup_execute_encryption(caller, *, attestation_document, claims_raw, execution_id):
    mock_encryption = MagicMock()
    mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
    mock_encryption.client_public_key_bytes = b"fake_public_key"
    decrypted = {
        "execution_id": execution_id,
        "attestation_document": attestation_document,
        "status": "accepted",
    }
    if claims_raw is not None:
        decrypted["claims_raw"] = claims_raw
    mock_encryption.decrypt_response = MagicMock(return_value=decrypted)
    caller._encryption = mock_encryption
    caller._oidc_token = "fake_oidc_token"
    return mock_encryption


# ---------------------------------------------------------------------------
# Test A (tasks.md 6.2): wrong script_env_hash rejected
# ---------------------------------------------------------------------------


class TestWrongScriptEnvHashRejected:
    """A wrong script_env_hash in a well-formed, correctly-bound claims_raw is
    rejected by validate_execution_attestation + request binding."""

    def test_wrong_script_env_hash_rejected(self):
        caller = _make_caller()
        script_env = {"GITHUB_TOKEN": "ghs_abc123"}
        claims = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": "0" * 64,  # wrong
        }
        payload_doc, claims_raw = _make_claims_fixture(claims, execution_id="exec-123")
        _setup_execute_encryption(
            caller,
            attestation_document="fake_attestation_b64",
            claims_raw=claims_raw,
            execution_id="exec-123",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch("call_remote_executor.attestation.validate_attestation", return_value=payload_doc):
            with pytest.raises(CallerError, match="script_env_hash"):
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                    script_env=script_env,
                )


# ---------------------------------------------------------------------------
# Test B (tasks.md 6.3): absent script_env_hash rejected
# ---------------------------------------------------------------------------


class TestAbsentScriptEnvHashRejected:
    """An absent script_env_hash in an otherwise well-formed, correctly-bound
    claims_raw is rejected — the fail-closed teeth for the removed None-guard."""

    def test_absent_script_env_hash_rejected(self):
        claims = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            # script_env_hash intentionally absent
        }
        payload_doc, claims_raw = _make_claims_fixture(claims)

        with patch("call_remote_executor.attestation.validate_attestation", return_value=payload_doc):
            with pytest.raises(CallerError, match="script_env_hash"):
                attestation.validate_execution_attestation(
                    "unused_attestation_b64", claims_raw, ROOT_CERT_PEM, EXPECTED_PCRS
                )


# ---------------------------------------------------------------------------
# Test C (tasks.md 6.4): /attest not subjected to the claims binding
# ---------------------------------------------------------------------------


class TestAttestNotSubjectedToClaimsBinding:
    """An /attest attestation carrying no claims_raw still validates via
    validate_attestation and is NOT subjected to the claims binding."""

    def test_attest_not_subjected_to_claims_binding(self):
        caller = _make_caller()

        fake_composite_key = b"\x01" * 32
        payload_doc = {"public_key": hashlib.sha256(fake_composite_key).hexdigest()}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "attestation_document": "fake_attestation_b64",
            "server_public_key": base64.b64encode(fake_composite_key).decode("ascii"),
        }

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch.object(caller, "validate_attestation", return_value=payload_doc), \
             patch("call_remote_executor.caller.ClientEncryption") as mock_encryption_cls, \
             patch("call_remote_executor.attestation._verify_claims_binding") as mock_binding:
            mock_encryption_cls.verify_server_key_fingerprint = MagicMock()
            caller.attest()

        mock_binding.assert_not_called()


# ---------------------------------------------------------------------------
# Missing claims_raw on /execute (tasks.md 6.5)
# ---------------------------------------------------------------------------


class TestMissingClaimsRawOnExecuteRejected:
    """Absent claims_raw on /execute is rejected (fail closed) regardless of
    stated cause — the server's conditional omission for its own test doubles
    MUST NOT be mirrored as caller-side optionality."""

    def test_missing_claims_raw_on_execute_rejected(self):
        caller = _make_caller()
        claims = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": RemoteExecutorCaller._compute_script_env_hash(None),
        }
        payload_doc, _claims_raw = _make_claims_fixture(claims)
        _setup_execute_encryption(
            caller,
            attestation_document="fake_attestation_b64",
            claims_raw=None,  # intentionally absent
            execution_id="exec-123",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch("call_remote_executor.attestation.validate_attestation", return_value=payload_doc):
            with pytest.raises(CallerError, match="claims_raw"):
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                )


# ---------------------------------------------------------------------------
# Version gate (tasks.md 6.6)
# ---------------------------------------------------------------------------


class TestVersionGate:
    """Unknown envelope v, unknown schema_version MAJOR, and unknown digest
    algorithm prefix are each rejected; a higher MINOR and an unknown additive
    field are tolerated."""

    def test_unknown_envelope_version_rejected(self):
        claims = {"schema_version": "1.0", "output_digest": SHA256_DIGEST_PREFIX + "0" * 64}
        payload_doc, claims_raw = _make_claims_fixture(claims, v=2)
        with pytest.raises(CallerError, match="envelope version"):
            attestation._verify_claims_binding(payload_doc, claims_raw, phase="test")

    def test_unknown_schema_major_rejected(self):
        claims = {"schema_version": "2.0", "output_digest": SHA256_DIGEST_PREFIX + "0" * 64}
        payload_doc, claims_raw = _make_claims_fixture(claims)
        with pytest.raises(CallerError, match="schema_version MAJOR"):
            attestation._verify_claims_binding(payload_doc, claims_raw, phase="test")

    def test_unknown_digest_algorithm_prefix_rejected(self):
        claims = {"schema_version": "1.0", "output_digest": SHA256_DIGEST_PREFIX + "0" * 64}
        claims_json = json.dumps(claims).encode("utf-8")
        claims_raw = base64.b64encode(claims_json).decode("ascii")
        bad_digest = "sha1:" + hashlib.sha256(claims_json).hexdigest()
        envelope = {
            "v": 1,
            "claims_digest": bad_digest,
            "timestamp": "2026-01-01T00:00:00Z",
            "execution_id": "exec-fixture",
        }
        payload_doc = {"user_data": json.dumps(envelope).encode("utf-8")}
        with pytest.raises(CallerError, match="algorithm prefix"):
            attestation._verify_claims_binding(payload_doc, claims_raw, phase="test")

    def test_higher_minor_tolerated(self):
        claims = {
            "schema_version": "1.9",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": "a" * 64,
        }
        payload_doc, claims_raw = _make_claims_fixture(claims)
        result = attestation._verify_claims_binding(payload_doc, claims_raw, phase="test")
        assert result["schema_version"] == "1.9"

    def test_unknown_additive_field_tolerated(self):
        claims = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": "a" * 64,
            "gpu": {"model": "H100", "count": 8},
        }
        payload_doc, claims_raw = _make_claims_fixture(claims)
        result = attestation._verify_claims_binding(payload_doc, claims_raw, phase="test")
        assert result["gpu"] == {"model": "H100", "count": 8}


# ---------------------------------------------------------------------------
# Output digest (tasks.md 6.7)
# ---------------------------------------------------------------------------


def _build_output_payload_doc(output_digest: str, envelope_v: int = 1) -> tuple[dict, str]:
    claims = {"schema_version": "1.0", "output_digest": output_digest}
    claims_json = json.dumps(claims).encode("utf-8")
    claims_raw = base64.b64encode(claims_json).decode("ascii")
    claims_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(claims_json).hexdigest()
    envelope = {
        "v": envelope_v,
        "claims_digest": claims_digest,
        "timestamp": "2026-01-01T00:00:00Z",
        "execution_id": "exec-fixture",
    }
    payload_doc = {
        "module_id": "mod",
        "digest": "SHA384",
        "timestamp": 0,
        "nitrotpm_pcrs": {},
        "certificate": b"",
        "cabundle": [],
        "user_data": json.dumps(envelope).encode("utf-8"),
    }
    return payload_doc, claims_raw


class TestOutputDigestRecompute:
    """validate_output_attestation accepts the canonical-JSON output_digest and
    rejects a mismatch; only output_digest is required (no execution-field
    false-reject)."""

    def test_output_digest_accepted(self):
        stdout, stderr, exit_code = "hello\n", "", 0
        canonical = json.dumps(
            {"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            sort_keys=True,
            separators=(",", ":"),
        )
        correct_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        payload_doc, claims_raw = _build_output_payload_doc(correct_digest)
        attestation_b64 = _build_fake_attestation_b64(payload_doc)

        result = attestation.validate_output_attestation(
            attestation_b64, claims_raw, stdout, stderr, exit_code,
            root_cert_pem="", expected_pcrs=None, expected_nonce=None,
        )
        assert result is True

    def test_output_digest_mismatch_rejected(self):
        stdout, stderr, exit_code = "hello\n", "", 0
        wrong_digest = SHA256_DIGEST_PREFIX + "0" * 64
        payload_doc, claims_raw = _build_output_payload_doc(wrong_digest)
        attestation_b64 = _build_fake_attestation_b64(payload_doc)

        with pytest.raises(CallerError, match="digest mismatch"):
            attestation.validate_output_attestation(
                attestation_b64, claims_raw, stdout, stderr, exit_code,
                root_cert_pem="", expected_pcrs=None, expected_nonce=None,
            )

    def test_output_claims_require_only_output_digest(self):
        """No false-reject on the four execution fields, which are legitimately
        absent from output claims (design D6, resolved OQ1)."""
        stdout, stderr, exit_code = "ok\n", "", 0
        canonical = json.dumps(
            {"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            sort_keys=True,
            separators=(",", ":"),
        )
        correct_digest = SHA256_DIGEST_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        payload_doc, claims_raw = _build_output_payload_doc(correct_digest)

        decoded_claims = json.loads(base64.b64decode(claims_raw))
        assert "repository_url" not in decoded_claims

        attestation_b64 = _build_fake_attestation_b64(payload_doc)
        result = attestation.validate_output_attestation(
            attestation_b64, claims_raw, stdout, stderr, exit_code,
            root_cert_pem="", expected_pcrs=None, expected_nonce=None,
        )
        assert result is True
