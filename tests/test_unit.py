"""Unit tests for marker parsing, allowlist validation, and provenance manifest.

Validates: Requirements 2.4, 2.5, 3.4, 4.4, 4.7, 4.8, 5.2
"""

from scripts.parse_markers import parse_sha256_marker, parse_artifact_name_marker
from scripts.validate_allowlist import validate_server_url
from scripts.create_provenance import create_provenance_manifest

import pytest


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
            "BINARY_ARTIFACT_NAME:attested-hello-abc123\n"
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
            "BINARY_ARTIFACT_NAME:attested-hello-abc123\n"
        )
        with pytest.raises(ValueError, match="BINARY_SHA256 marker not found"):
            parse_sha256_marker(stdout)


class TestParseArtifactNameMarker:
    """Validates: Requirements 2.5, 4.4"""

    def test_parse_artifact_name_marker(self):
        """Parse a known BINARY_ARTIFACT_NAME marker from sample stdout."""
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "BINARY_SHA256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
            "BINARY_ARTIFACT_NAME:attested-hello-abc123\n"
            "Done.\n"
        )
        result = parse_artifact_name_marker(stdout)
        assert result == "attested-hello-abc123"

    def test_missing_artifact_name_marker_raises(self):
        """ValueError raised when BINARY_ARTIFACT_NAME marker is missing."""
        stdout = (
            "Compiling attested-hello v0.1.0\n"
            "BINARY_SHA256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
        )
        with pytest.raises(ValueError, match="BINARY_ARTIFACT_NAME marker not found"):
            parse_artifact_name_marker(stdout)


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
            "attestation-documents/output-integrity-poll-001.b64"
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
