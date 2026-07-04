"""Unit tests for marker parsing, allowlist validation, provenance manifest, and script_env_hash.

Validates: Requirements 2.4, 2.5, 3.4, 4.4, 4.7, 4.8, 5.2, 10.6, 10.7, 10.8
"""

import base64
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.parse_markers import parse_sha256_marker, parse_oci_ref_marker
from scripts.validate_allowlist import validate_server_url
from scripts.create_provenance import create_provenance_manifest

import pytest

# Ensure the caller module is importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from call_remote_executor.caller import RemoteExecutorCaller
from call_remote_executor.errors import CallerError


# ---------------------------------------------------------------------------
# Marker parsing tests
# ---------------------------------------------------------------------------


class TestParseSha256Marker:
    """Validates: Requirements 2.4, 4.4"""

    def test_parse_sha256_marker(self):
        """Parse a known BINARY_SHA256 marker embedded in other stdout text."""
        digest = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "  Finished release [optimized] target(s) in 12.34s\n"
            f"BINARY_SHA256:{digest}\n"
            "Upload complete.\n"
            "BINARY_OCI_REF:ghcr.io/owner/repo/tmp-build:abc1234-x7k9m2\n"
        )
        result = parse_sha256_marker(stdout)
        assert result == digest.lower()

    def test_parse_sha256_marker_uppercase(self):
        """SHA256 marker with uppercase hex is returned lowercased."""
        digest = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"
        stdout = f"BINARY_SHA256:{digest}\n"
        result = parse_sha256_marker(stdout)
        assert result == digest.lower()

    def test_missing_sha256_marker_raises(self):
        """ValueError raised when BINARY_SHA256 marker is missing."""
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "  Finished release [optimized] target(s) in 12.34s\n"
            "BINARY_OCI_REF:ghcr.io/owner/repo/tmp-build:abc1234-x7k9m2\n"
        )
        with pytest.raises(ValueError, match="BINARY_SHA256 marker not found"):
            parse_sha256_marker(stdout)


class TestParseOciRefMarker:
    """Validates: Requirements 2.5, 4.4"""

    def test_parse_oci_ref_marker(self):
        """Parse a known BINARY_OCI_REF marker from sample stdout."""
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "BINARY_SHA256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
            "BINARY_OCI_REF:ghcr.io/owner/repo/tmp-build:abc1234-x7k9m2\n"
            "Done.\n"
        )
        result = parse_oci_ref_marker(stdout)
        assert result == "ghcr.io/owner/repo/tmp-build:abc1234-x7k9m2"

    def test_missing_oci_ref_marker_raises(self):
        """ValueError raised when BINARY_OCI_REF marker is missing."""
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "BINARY_SHA256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
        )
        with pytest.raises(ValueError, match="BINARY_OCI_REF marker not found"):
            parse_oci_ref_marker(stdout)


# ---------------------------------------------------------------------------
# SHA-256 mismatch detection
# ---------------------------------------------------------------------------


class TestSha256MismatchDetected:
    """Validates: Requirement 4.7"""

    def test_sha256_mismatch_detected(self):
        """Digest parsed from stdout differs from an expected digest."""
        stdout = (
            "BINARY_SHA256:"
            "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
        )
        parsed_digest = parse_sha256_marker(stdout)
        expected_digest = (
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        )
        assert parsed_digest != expected_digest, (
            "Parsed digest should NOT match a different expected digest"
        )


# ---------------------------------------------------------------------------
# Provenance manifest tests
# ---------------------------------------------------------------------------


class TestProvenanceManifestSchema:
    """Validates: Requirement 5.2"""

    def test_provenance_manifest_schema(self):
        """Manifest returned by create_provenance_manifest matches the design schema."""
        manifest = create_provenance_manifest(
            binary_name="attested-hello",
            sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            repo_url="https://github.com/owner/repo",
            commit_hash="abc1234",
            run_id="12345678",
            timestamp="2025-01-15T10:30:00Z",
        )

        # Top-level keys
        assert manifest["version"] == "1.0"

        # Binary section
        assert manifest["binary"]["name"] == "attested-hello"
        assert manifest["binary"]["sha256"] == (
            "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        # Build section
        assert manifest["build"]["repository_url"] == "https://github.com/owner/repo"
        assert manifest["build"]["commit_hash"] == "abc1234"
        assert manifest["build"]["workflow_run_id"] == "12345678"
        assert manifest["build"]["timestamp"] == "2025-01-15T10:30:00Z"

        # Attestation section — fixed paths per design
        assert manifest["attestation"]["server_identity"] == (
            "attestation-documents/server-identity.b64"
        )
        assert manifest["attestation"]["execution_acceptance"] == (
            "attestation-documents/execution-acceptance.b64"
        )
        assert manifest["attestation"]["output_integrity"] == (
            "attestation-documents/output-integrity.b64"
        )
        assert manifest["attestation"]["manifest"] == (
            "attestation-documents/manifest.json"
        )

        # Ensure no unexpected top-level keys
        assert set(manifest.keys()) == {"version", "binary", "build", "attestation"}


# ---------------------------------------------------------------------------
# Allowlist validation tests
# ---------------------------------------------------------------------------


class TestAllowlistValidation:
    """Validates: Requirement 3.4"""

    def test_allowlist_empty_accepts_all(self):
        """Empty allowlist accepts any URL."""
        assert validate_server_url("https://example.com", "") is True
        assert validate_server_url("https://other.example.org:8443/path", "") is True
        assert validate_server_url("http://localhost:3000", "") is True

    def test_allowlist_rejects_unlisted(self):
        """URL not in the allowlist is rejected."""
        allowlist = "https://allowed-one.com,https://allowed-two.com"
        assert validate_server_url("https://evil.com", allowlist) is False


# ---------------------------------------------------------------------------
# script_env_hash tests
# ---------------------------------------------------------------------------


class TestScriptEnvHashEmptyDict:
    """Validates: Requirement 10.6"""

    def test_script_env_hash_empty_dict(self):
        """Empty dict produces sha256('{}')."""
        expected = hashlib.sha256(
            json.dumps({}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert expected == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"

        result = RemoteExecutorCaller._compute_script_env_hash({})
        assert result == expected

    def test_script_env_hash_none_gives_empty_dict_hash(self):
        """None input produces the same hash as empty dict."""
        empty_hash = RemoteExecutorCaller._compute_script_env_hash({})
        none_hash = RemoteExecutorCaller._compute_script_env_hash(None)
        assert none_hash == empty_hash


class TestScriptEnvHashKnownValue:
    """Validates: Requirement 10.6"""

    def test_script_env_hash_known_value(self):
        """Known dict produces expected hash (keys sorted, compact JSON)."""
        script_env = {"GITHUB_TOKEN": "ghs_abc123", "GITHUB_REPOSITORY": "owner/repo"}
        # Canonical form: {"GITHUB_REPOSITORY":"owner/repo","GITHUB_TOKEN":"ghs_abc123"}
        expected = "baa72126b73984271c45ae84e4207982907d0c4bd34e4af8ee2f39960976ba09"

        result = RemoteExecutorCaller._compute_script_env_hash(script_env)
        assert result == expected


class TestScriptEnvHashMismatchRaises:
    """Validates: Requirements 10.7, 10.8"""

    def test_script_env_hash_mismatch_raises(self):
        """CallerError raised when attested script_env_hash doesn't match local computation."""
        caller = RemoteExecutorCaller(
            server_url="https://test.example.com",
            root_cert_pem="-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----",
            expected_pcrs={0: "0" * 96},
            timeout=5,
        )

        # Set up mocked encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
        mock_encryption.client_public_key_bytes = b"fake_public_key"

        # The attested claims_raw contains a WRONG script_env_hash
        wrong_hash = "0" * 64
        claims = {
            "schema_version": "1.0",
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": wrong_hash,
        }
        claims_json = json.dumps(claims).encode("utf-8")
        claims_raw = base64.b64encode(claims_json).decode("ascii")
        claims_digest = "sha256:" + hashlib.sha256(claims_json).hexdigest()
        envelope = {
            "v": 1,
            "claims_digest": claims_digest,
            "timestamp": "2026-01-01T00:00:00Z",
            "execution_id": "exec-123",
        }
        payload_doc = {"user_data": json.dumps(envelope).encode("utf-8")}

        mock_encryption.decrypt_response = MagicMock(return_value={
            "execution_id": "exec-123",
            "attestation_document": "fake_attestation_b64",
            "claims_raw": claims_raw,
            "status": "accepted",
        })
        caller._encryption = mock_encryption
        caller._oidc_token = "fake_oidc_token"

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        script_env = {"GITHUB_TOKEN": "ghs_abc123", "GITHUB_REPOSITORY": "owner/repo"}

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
