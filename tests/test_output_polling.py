"""Unit tests for final-only output attestation and rate limiting tolerance.

Validates: Requirements 13.1, 13.2, 13.3
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure the caller module is importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from call_remote_executor.caller import RemoteExecutorCaller
from call_remote_executor.errors import CallerError


def _make_caller(**kwargs):
    """Create a RemoteExecutorCaller with mocked encryption for testing."""
    defaults = {
        "server_url": "https://test.example.com",
        "root_cert_pem": "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----",
        "expected_pcrs": {0: "0" * 96},
        "timeout": 5,
        "poll_interval": 0,  # No delay in tests
        "max_poll_duration": 10,
    }
    defaults.update(kwargs)
    caller = RemoteExecutorCaller(**defaults)
    return caller


def _setup_encryption(caller, decrypt_side_effect):
    """Attach a mocked encryption object to the caller.

    decrypt_side_effect can be a list of dicts (one per poll) or a single dict.
    """
    mock_encryption = MagicMock()
    mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
    if isinstance(decrypt_side_effect, list):
        mock_encryption.decrypt_response = MagicMock(side_effect=decrypt_side_effect)
    else:
        mock_encryption.decrypt_response = MagicMock(return_value=decrypt_side_effect)
    caller._encryption = mock_encryption
    return mock_encryption


class TestPollOutputValidatesAttestationOnlyOnFinal:
    """Validates: Requirement 13.1

    Verify that intermediate poll responses with output_attestation_document
    present do NOT trigger validation or artifact saving.
    """

    def test_poll_output_validates_attestation_only_on_final(self):
        """Intermediate polls with attestation present should NOT trigger validation.

        We simulate two polls:
        1. Intermediate poll: complete=false, output_attestation_document present
        2. Final poll: complete=true, output_attestation_document present

        validate_output_attestation should only be called ONCE (on the final poll).
        """
        caller = _make_caller()

        # Two poll responses: intermediate (not complete) then final (complete)
        intermediate_response = {
            "stdout": "building...\n",
            "stderr": "",
            "exit_code": None,
            "complete": False,
            "output_attestation_document": "intermediate_attestation_b64",
        }
        final_response = {
            "stdout": "building...\ndone!\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": "final_attestation_b64",
        }

        _setup_encryption(caller, [intermediate_response, final_response])

        # Mock HTTP responses (both return 200 with encrypted_response)
        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response), \
             patch.object(caller, "validate_output_attestation", return_value=True) as mock_validate:
            result = caller.poll_output("exec-123")

        # validate_output_attestation should be called exactly once (on the final poll)
        assert mock_validate.call_count == 1
        # It should be called with the FINAL attestation, not the intermediate one
        mock_validate.assert_called_once_with(
            "final_attestation_b64",
            "building...\ndone!\n",
            "",
            0,
            expected_nonce=mock_validate.call_args[1]["expected_nonce"],
        )
        assert result["output_integrity_status"] == "pass"
        assert result["exit_code"] == 0

    def test_intermediate_attestation_not_saved_as_artifact(self):
        """Intermediate poll attestation should NOT be saved as artifact."""
        caller = _make_caller(attestation_output_dir="/tmp/test-artifacts")

        intermediate_response = {
            "stdout": "step 1\n",
            "stderr": "",
            "exit_code": None,
            "complete": False,
            "output_attestation_document": "intermediate_attestation_b64",
        }
        final_response = {
            "stdout": "step 1\nstep 2\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": "final_attestation_b64",
        }

        _setup_encryption(caller, [intermediate_response, final_response])

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        # Mock the artifact collector
        mock_collector = MagicMock()
        caller._artifact_collector = mock_collector

        with patch("requests.post", return_value=mock_http_response), \
             patch.object(caller, "validate_output_attestation", return_value=True):
            result = caller.poll_output("exec-123")

        # save_output_integrity should be called exactly once (on the final poll)
        assert mock_collector.save_output_integrity.call_count == 1
        # Verify it was called with the final attestation
        call_kwargs = mock_collector.save_output_integrity.call_args[1]
        assert call_kwargs["attestation_b64"] == "final_attestation_b64"


class TestPollOutputRateLimitedAttestationDoesNotFail:
    """Validates: Requirements 13.2, 13.3

    Verify that when the final poll has output_attestation_document: null and
    attestation_rate_limited: true, the caller does NOT raise CallerError and
    returns output_integrity_status = "rate_limited".
    """

    def test_poll_output_rate_limited_attestation_does_not_fail(self):
        """Rate-limited attestation on final poll should not raise CallerError."""
        caller = _make_caller()

        final_response = {
            "stdout": "hello world\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": None,
            "attestation_rate_limited": True,
        }

        _setup_encryption(caller, final_response)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response):
            result = caller.poll_output("exec-456")

        assert result["output_integrity_status"] == "rate_limited"
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello world\n"
        assert result["output_attestation_document"] is None

    def test_poll_output_rate_limited_after_intermediate_polls(self):
        """Rate limiting on final poll after intermediate polls should still succeed."""
        caller = _make_caller()

        intermediate_response = {
            "stdout": "working...\n",
            "stderr": "",
            "exit_code": None,
            "complete": False,
            "output_attestation_document": "intermediate_attestation_b64",
        }
        final_response = {
            "stdout": "working...\ndone\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": None,
            "attestation_rate_limited": True,
        }

        _setup_encryption(caller, [intermediate_response, final_response])

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response):
            result = caller.poll_output("exec-789")

        assert result["output_integrity_status"] == "rate_limited"
        assert result["exit_code"] == 0


class TestPollOutputMissingAttestationWithoutRateLimitFails:
    """Validates: Requirements 13.2, 13.3

    Verify that when the final poll has output_attestation_document: null
    without attestation_rate_limited: true, the caller raises CallerError
    (unless allow_missing_output_attestation is set).
    """

    def test_poll_output_missing_attestation_without_rate_limit_fails(self):
        """Missing attestation without rate_limited flag should raise CallerError."""
        caller = _make_caller()

        final_response = {
            "stdout": "output\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": None,
            # No attestation_rate_limited field
        }

        _setup_encryption(caller, final_response)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response):
            with pytest.raises(CallerError, match="Output attestation is missing"):
                caller.poll_output("exec-missing")

    def test_poll_output_missing_attestation_rate_limited_false_fails(self):
        """attestation_rate_limited=False should still fail (only True is tolerance)."""
        caller = _make_caller()

        final_response = {
            "stdout": "output\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": None,
            "attestation_rate_limited": False,
        }

        _setup_encryption(caller, final_response)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response):
            with pytest.raises(CallerError, match="Output attestation is missing"):
                caller.poll_output("exec-false-flag")

    def test_poll_output_missing_attestation_allowed_returns_skipped(self):
        """With allow_missing_output_attestation=True, missing attestation returns skipped."""
        caller = _make_caller(allow_missing_output_attestation=True)

        final_response = {
            "stdout": "output\n",
            "stderr": "",
            "exit_code": 0,
            "complete": True,
            "output_attestation_document": None,
        }

        _setup_encryption(caller, final_response)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch("requests.post", return_value=mock_http_response):
            result = caller.poll_output("exec-allowed")

        assert result["output_integrity_status"] == "skipped"
        assert result["exit_code"] == 0
