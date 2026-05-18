"""Unit tests for execution_id binding and encrypted error envelopes.

Validates: Requirements 10.10, 10.11, 10.12, 10.14
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the caller module is importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from call_remote_executor.caller import RemoteExecutorCaller
from call_remote_executor.errors import CallerError


def _make_caller():
    """Create a RemoteExecutorCaller with mocked encryption for testing."""
    caller = RemoteExecutorCaller(
        server_url="https://test.example.com",
        root_cert_pem="-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----",
        expected_pcrs={0: "0" * 96},
        timeout=5,
    )
    caller._oidc_token = "fake_oidc_token"
    return caller


def _setup_encryption(caller, decrypt_return_value):
    """Attach a mocked encryption object to the caller."""
    mock_encryption = MagicMock()
    mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
    mock_encryption.client_public_key_bytes = b"fake_public_key"
    mock_encryption.decrypt_response = MagicMock(return_value=decrypt_return_value)
    caller._encryption = mock_encryption
    return mock_encryption


# ---------------------------------------------------------------------------
# execution_id binding tests
# ---------------------------------------------------------------------------


class TestExecutionIdBindingVerified:
    """Validates: Requirements 10.10, 10.11 (happy path)"""

    def test_execution_id_binding_verified(self):
        """Verify attested execution_id matches response body execution_id (happy path).

        When the attestation user_data contains an execution_id that matches
        the execution_id in the decrypted response body, execute() should
        succeed without raising.
        """
        caller = _make_caller()

        execution_id = "exec-abc-123"
        attested_user_data = json.dumps({
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": caller._compute_script_env_hash(None),
            "execution_id": execution_id,
        })

        _setup_encryption(caller, {
            "execution_id": execution_id,
            "attestation_document": "fake_attestation_b64",
            "status": "accepted",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        mock_payload = {"user_data": attested_user_data}

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch.object(caller, "validate_attestation", return_value=mock_payload):
            result = caller.execute(
                repository_url="https://github.com/owner/repo",
                commit_hash="abc123",
                script_path="scripts/build.sh",
                github_token="ghp_token",
                script_env=None,
            )

        assert result["execution_id"] == execution_id


class TestExecutionIdMismatchRaises:
    """Validates: Requirements 10.10, 10.11"""

    def test_execution_id_mismatch_raises(self):
        """Verify CallerError raised when attested execution_id differs from response body.

        The attestation user_data contains execution_id "exec-attested-999" but
        the decrypted response body contains "exec-response-111". This mismatch
        must raise CallerError.
        """
        caller = _make_caller()

        attested_user_data = json.dumps({
            "repository_url": "https://github.com/owner/repo",
            "commit_hash": "abc123",
            "script_path": "scripts/build.sh",
            "script_env_hash": caller._compute_script_env_hash(None),
            "execution_id": "exec-attested-999",
        })

        _setup_encryption(caller, {
            "execution_id": "exec-response-111",
            "attestation_document": "fake_attestation_b64",
            "status": "accepted",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        mock_payload = {"user_data": attested_user_data}

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch.object(caller, "validate_attestation", return_value=mock_payload):
            with pytest.raises(CallerError, match="execution_id"):
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                    script_env=None,
                )


# ---------------------------------------------------------------------------
# Encrypted error envelope tests
# ---------------------------------------------------------------------------


class TestEncryptedErrorEnvelopeDetected:
    """Validates: Requirements 10.12, 10.13"""

    def test_encrypted_error_envelope_detected(self):
        """Verify CallerError raised when decrypted /execute response contains `error` field.

        When the server returns HTTP 200 but the decrypted payload is an error
        envelope (contains "error" and "error_code" fields), execute() must
        raise CallerError with the error message before attempting attestation
        validation.
        """
        caller = _make_caller()

        # The decrypted response is an error envelope — no attestation_document
        _setup_encryption(caller, {
            "error": "OIDC token validation failed: repository claim mismatch",
            "error_code": "OIDC_VALIDATION_FAILED",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch.object(caller, "_request_with_retry", return_value=mock_response):
            with pytest.raises(CallerError, match="OIDC token validation failed") as exc_info:
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                )

        # Verify the error details are preserved
        assert exc_info.value.details["error_code"] == "OIDC_VALIDATION_FAILED"

    def test_encrypted_error_envelope_precedes_attestation_check(self):
        """Error envelope detection must happen BEFORE attestation_document extraction.

        Even if the decrypted payload also contains an attestation_document field,
        the error field takes precedence and CallerError is raised immediately.
        """
        caller = _make_caller()

        _setup_encryption(caller, {
            "error": "Nonce already used",
            "error_code": "NONCE_DUPLICATE",
            "attestation_document": "should_not_be_checked",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch.object(caller, "_request_with_retry", return_value=mock_response):
            with pytest.raises(CallerError, match="Nonce already used"):
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                )


class TestEncryptedErrorEnvelopeOnPoll:
    """Validates: Requirement 10.14"""

    def test_encrypted_error_envelope_on_poll(self):
        """Verify CallerError raised when decrypted /output response contains `error` field.

        When polling for output, if the server returns HTTP 200 but the decrypted
        payload is an error envelope, poll_output() must raise CallerError with
        the error message.
        """
        caller = _make_caller()

        # Set up encryption that returns an error envelope on decrypt
        mock_encryption = MagicMock()
        mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
        mock_encryption.decrypt_response = MagicMock(return_value={
            "error": "Execution not found or expired",
            "error_code": "EXECUTION_NOT_FOUND",
        })
        caller._encryption = mock_encryption

        # Mock the HTTP response as 200 with encrypted_response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(CallerError, match="Execution not found or expired") as exc_info:
                caller.poll_output("exec-123")

        assert exc_info.value.details["error_code"] == "EXECUTION_NOT_FOUND"
        assert exc_info.value.details["execution_id"] == "exec-123"
