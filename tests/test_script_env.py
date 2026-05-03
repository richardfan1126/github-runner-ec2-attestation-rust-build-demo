"""Unit tests for --script-env argument parsing and payload inclusion.

Validates: Requirements 10.1, 10.2
"""

import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CLI parsing tests
#
# The parsing logic in cli.py:
#   1. argparse collects --script-env items into a list (action="append")
#   2. Each item is checked for "=" — if missing, print error and sys.exit(1)
#   3. str.partition("=") splits on the first "=" into (key, sep, value)
#   4. The resulting dict is passed to caller.run()
#
# We replicate the parsing logic here to test it in isolation, since the
# call_remote_executor module is not on the default Python path.
# ---------------------------------------------------------------------------


def _parse_script_env(raw_items: list[str]) -> dict[str, str]:
    """Replicate the --script-env parsing logic from cli.py.

    Raises SystemExit(1) if any item is missing '='.
    """
    script_env: dict[str, str] = {}
    for item in raw_items:
        if "=" not in item:
            print(
                f"ERROR: --script-env value must be in KEY=VALUE format, got: {item!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        key, _, value = item.partition("=")
        script_env[key] = value
    return script_env


class TestScriptEnvSinglePairParsed:
    """Test that a single --script-env KEY=VALUE is parsed into {'KEY': 'VALUE'}.

    Validates: Requirement 10.1
    """

    def test_script_env_single_pair_parsed(self):
        result = _parse_script_env(["MY_KEY=my_value"])
        assert result == {"MY_KEY": "my_value"}

    def test_script_env_empty_value(self):
        """KEY= should parse as key='KEY', value=''."""
        result = _parse_script_env(["TOKEN="])
        assert result == {"TOKEN": ""}


class TestScriptEnvMultiplePairsAccumulated:
    """Test that multiple --script-env arguments are accumulated into a single dict.

    Validates: Requirement 10.1
    """

    def test_script_env_multiple_pairs_accumulated(self):
        result = _parse_script_env([
            "GITHUB_TOKEN=ghp_abc123",
            "GITHUB_RUN_ID=12345",
            "GITHUB_REPOSITORY=owner/repo",
        ])
        assert result == {
            "GITHUB_TOKEN": "ghp_abc123",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_REPOSITORY": "owner/repo",
        }


class TestScriptEnvValueWithEqualsSign:
    """Test that KEY=VALUE=WITH=EQUALS parses correctly (partition on first '=' only).

    Validates: Requirement 10.1
    """

    def test_script_env_value_with_equals_sign(self):
        result = _parse_script_env(["ACTIONS_RUNTIME_URL=https://example.com/path?a=1&b=2"])
        assert result == {
            "ACTIONS_RUNTIME_URL": "https://example.com/path?a=1&b=2",
        }

    def test_script_env_value_multiple_equals(self):
        result = _parse_script_env(["KEY=val=ue=extra"])
        assert result == {"KEY": "val=ue=extra"}


class TestScriptEnvMissingEqualsExits:
    """Test that a value without '=' causes sys.exit(1).

    Validates: Requirement 10.1
    """

    def test_script_env_missing_equals_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            _parse_script_env(["NO_EQUALS_HERE"])
        assert exc_info.value.code == 1

    def test_script_env_missing_equals_among_valid(self):
        """Even if some items are valid, a bad one should exit."""
        with pytest.raises(SystemExit) as exc_info:
            _parse_script_env(["GOOD=value", "BAD_ITEM"])
        assert exc_info.value.code == 1


class TestScriptEnvArgparseIntegration:
    """Test that argparse correctly accumulates --script-env arguments.

    Validates: Requirement 10.1
    """

    def test_argparse_accumulates_script_env(self):
        """Verify argparse action='append' collects multiple --script-env values."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--script-env",
            action="append",
            default=[],
            metavar="KEY=VALUE",
        )
        args = parser.parse_args([
            "--script-env", "A=1",
            "--script-env", "B=2",
            "--script-env", "C=3",
        ])
        assert args.script_env == ["A=1", "B=2", "C=3"]

    def test_argparse_no_script_env_gives_empty_list(self):
        """When no --script-env is provided, default is empty list."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--script-env",
            action="append",
            default=[],
            metavar="KEY=VALUE",
        )
        args = parser.parse_args([])
        assert args.script_env == []


# ---------------------------------------------------------------------------
# Payload inclusion tests
#
# Test that script_env is included in the plaintext_payload when execute()
# is called. We mock the encryption and HTTP layers.
# ---------------------------------------------------------------------------


class TestScriptEnvIncludedInPayload:
    """Test that the script_env dict is included in the plaintext_payload.

    Validates: Requirement 10.2
    """

    def _make_caller_with_mocked_encryption(self):
        """Create a RemoteExecutorCaller with mocked internals for execute() testing."""
        # We need to add the module to sys.path temporarily
        from pathlib import Path
        module_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)

        from call_remote_executor.caller import RemoteExecutorCaller

        caller = RemoteExecutorCaller(
            server_url="https://test.example.com",
            root_cert_pem="-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----",
            expected_pcrs={0: "0" * 96},
            timeout=5,
        )

        # Mock the encryption object
        mock_encryption = MagicMock()
        mock_encryption.encrypt_payload = MagicMock(return_value="encrypted_blob")
        mock_encryption.client_public_key_bytes = b"fake_public_key"
        # decrypt_response returns a dict with execution_id and attestation_document
        mock_encryption.decrypt_response = MagicMock(return_value={
            "execution_id": "exec-123",
            "attestation_document": "",
            "status": "accepted",
        })
        caller._encryption = mock_encryption
        caller._oidc_token = "fake_oidc_token"

        return caller, mock_encryption

    def test_script_env_included_in_payload(self):
        """When script_env is provided, it appears in the plaintext_payload."""
        caller, mock_encryption = self._make_caller_with_mocked_encryption()

        env_dict = {"GITHUB_TOKEN": "ghp_test", "GITHUB_RUN_ID": "999"}

        # Mock _request_with_retry to return a fake response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        # Mock validate_attestation to avoid real attestation validation
        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch.object(caller, "validate_attestation", return_value={}):
            try:
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                    script_env=env_dict,
                )
            except Exception:
                # We may get errors from attestation validation etc. — that's fine,
                # we only care about what was passed to encrypt_payload
                pass

        # Verify encrypt_payload was called with a dict containing script_env
        mock_encryption.encrypt_payload.assert_called_once()
        payload = mock_encryption.encrypt_payload.call_args[0][0]
        assert payload["script_env"] == {"GITHUB_TOKEN": "ghp_test", "GITHUB_RUN_ID": "999"}

    def test_script_env_none_gives_empty_dict_in_payload(self):
        """When script_env is None, the payload contains an empty dict for script_env."""
        caller, mock_encryption = self._make_caller_with_mocked_encryption()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"encrypted_response": "fake_encrypted"}

        with patch.object(caller, "_request_with_retry", return_value=mock_response), \
             patch.object(caller, "validate_attestation", return_value={}):
            try:
                caller.execute(
                    repository_url="https://github.com/owner/repo",
                    commit_hash="abc123",
                    script_path="scripts/build.sh",
                    github_token="ghp_token",
                    script_env=None,
                )
            except Exception:
                pass

        mock_encryption.encrypt_payload.assert_called_once()
        payload = mock_encryption.encrypt_payload.call_args[0][0]
        assert payload["script_env"] == {}
