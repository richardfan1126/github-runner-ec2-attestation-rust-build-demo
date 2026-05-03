"""Smoke tests for workflow YAML and project configuration.

These tests verify static configuration files contain the expected
structure and values without requiring any external services.
"""

from pathlib import Path

import pytest
import yaml

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "attested-rust-build.yml"
CARGO_TOML_PATH = PROJECT_ROOT / "rust-project" / "Cargo.toml"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


@pytest.fixture
def workflow():
    """Load and parse the workflow YAML.

    Note: PyYAML parses the bare key `on` as boolean True,
    so we access it via True in the dict.
    """
    content = WORKFLOW_PATH.read_text()
    return yaml.safe_load(content)


def _get_on_key(workflow):
    """Get the 'on' trigger section, handling PyYAML's True-key quirk."""
    if "on" in workflow:
        return workflow["on"]
    return workflow[True]


class TestWorkflowYamlInputs:
    """Verify workflow_dispatch inputs in YAML (Requirement 3.1)."""

    def test_workflow_yaml_inputs(self, workflow):
        """Verify workflow_dispatch inputs are correctly defined."""
        on_section = _get_on_key(workflow)
        inputs = on_section["workflow_dispatch"]["inputs"]

        # server_url is required
        assert "server_url" in inputs
        assert inputs["server_url"]["required"] is True

        # Optional inputs with defaults
        assert "script_path" in inputs
        assert inputs["script_path"].get("required") is False
        assert inputs["script_path"]["default"] == "scripts/build-rust.sh"

        assert "commit_hash" in inputs
        assert inputs["commit_hash"].get("required") is False

        assert "repository_url" in inputs
        assert inputs["repository_url"].get("required") is False

        assert "audience" in inputs
        assert inputs["audience"].get("required") is False

        assert "server_url_allowlist" in inputs
        assert inputs["server_url_allowlist"].get("required") is False


class TestWorkflowYamlPermissions:
    """Verify permissions in YAML (Requirement 3.2)."""

    def test_workflow_yaml_permissions(self, workflow):
        """Verify the workflow requests the correct permissions."""
        permissions = workflow["permissions"]

        assert permissions["id-token"] == "write"
        assert permissions["contents"] == "read"
        assert permissions["packages"] == "write"
        assert permissions["attestations"] == "write"


class TestWorkflowYamlRootCert:
    """Verify ROOT_CERT_PEM env var in YAML (Requirement 8.4)."""

    def test_workflow_yaml_root_cert(self, workflow):
        """Verify ROOT_CERT_PEM environment variable is present and contains a certificate."""
        env = workflow["env"]

        assert "ROOT_CERT_PEM" in env
        root_cert = env["ROOT_CERT_PEM"]
        assert "-----BEGIN CERTIFICATE-----" in root_cert
        assert "-----END CERTIFICATE-----" in root_cert


class TestWorkflowYamlExpectedPcrs:
    """Verify EXPECTED_PCRS env var in YAML (Requirement 8.5)."""

    def test_workflow_yaml_expected_pcrs(self, workflow):
        """Verify EXPECTED_PCRS environment variable is present and contains valid JSON with PCR values."""
        import json

        env = workflow["env"]

        assert "EXPECTED_PCRS" in env
        pcrs_raw = env["EXPECTED_PCRS"]

        # Should be valid JSON
        pcrs = json.loads(pcrs_raw)
        assert isinstance(pcrs, dict)

        # Should contain at least one PCR entry with hex string values
        assert len(pcrs) > 0
        for key, value in pcrs.items():
            assert isinstance(value, str)
            # PCR values are hex strings
            int(value, 16)  # Raises ValueError if not valid hex


class TestWorkflowYamlCleanupStep:
    """Verify cleanup steps in YAML (Requirements 11.1, 11.2)."""

    def test_workflow_yaml_cleanup_step(self, workflow):
        """Verify cleanup steps: version ID lookup step and actions/delete-package-versions@v5
        step with if: always() and continue-on-error: true."""
        steps = workflow["jobs"]["attested-rust-build"]["steps"]

        # --- Find the version ID lookup step ---
        version_id_step = None
        for step in steps:
            if step.get("id") == "get_tmp_pkg_version":
                version_id_step = step
                break

        assert version_id_step is not None, (
            "Workflow must have a step with id 'get_tmp_pkg_version' to look up the temporary package version ID"
        )
        # Must run always (even on failure)
        assert version_id_step.get("if") == "always()", (
            "Version ID lookup step must have 'if: always()'"
        )
        # Must not fail the job
        assert version_id_step.get("continue-on-error") is True, (
            "Version ID lookup step must have 'continue-on-error: true'"
        )
        # Must use gh api to query package versions
        run_script = version_id_step.get("run", "")
        assert "gh api" in run_script, (
            "Version ID lookup step must use 'gh api' to query package versions"
        )
        assert "version_id" in run_script, (
            "Version ID lookup step must output 'version_id'"
        )

        # --- Find the delete-package-versions step ---
        delete_step = None
        for step in steps:
            uses = step.get("uses", "")
            if "actions/delete-package-versions@" in uses:
                delete_step = step
                break

        assert delete_step is not None, (
            "Workflow must have a step using 'actions/delete-package-versions@v5'"
        )
        assert "actions/delete-package-versions@v5" in delete_step.get("uses", ""), (
            "Cleanup step must use actions/delete-package-versions@v5"
        )
        # Must run always (conditional on version_id being available)
        step_if = delete_step.get("if", "")
        assert "always()" in step_if, (
            "Cleanup step must include 'always()' in its if condition"
        )
        # Must not fail the job
        assert delete_step.get("continue-on-error") is True, (
            "Cleanup step must have 'continue-on-error: true'"
        )
        # Must specify container package type
        with_section = delete_step.get("with", {})
        assert with_section.get("package-type") == "container", (
            "Cleanup step must specify 'package-type: container'"
        )
        # Must reference the version ID from the lookup step
        version_ids = str(with_section.get("package-version-ids", ""))
        assert "get_tmp_pkg_version" in version_ids, (
            "Cleanup step must reference version_id from get_tmp_pkg_version step"
        )


class TestCargoTomlBinaryTarget:
    """Verify Cargo.toml has attested-hello target (Requirement 1.1)."""

    def test_cargo_toml_binary_target(self):
        """Verify Cargo.toml defines a binary target named attested-hello."""
        content = CARGO_TOML_PATH.read_text()

        # Check package name
        assert 'name = "attested-hello"' in content

        # Check binary target section exists
        assert "[[bin]]" in content

        # Verify the binary target name
        lines = content.splitlines()
        in_bin_section = False
        found_bin_name = False
        for line in lines:
            if line.strip() == "[[bin]]":
                in_bin_section = True
            elif in_bin_section and line.startswith("["):
                in_bin_section = False
            elif in_bin_section and 'name = "attested-hello"' in line:
                found_bin_name = True
                break

        assert found_bin_name, "Cargo.toml must have a [[bin]] target named 'attested-hello'"


class TestPyprojectDependencies:
    """Verify pyproject.toml has caller dependencies (Requirement 8.1)."""

    def test_pyproject_dependencies(self):
        """Verify pyproject.toml declares all required caller dependencies."""
        content = PYPROJECT_PATH.read_text()

        required_deps = [
            "requests",
            "cbor2",
            "pycose",
            "pyOpenSSL",
            "pycryptodome",
            "cryptography",
            "wolfcrypt",
        ]

        for dep in required_deps:
            assert dep.lower() in content.lower(), (
                f"pyproject.toml must declare dependency: {dep}"
            )

    def test_pyproject_dev_dependencies(self):
        """Verify pyproject.toml declares dev dependencies."""
        content = PYPROJECT_PATH.read_text()

        required_dev_deps = [
            "hypothesis",
            "pytest",
            "pyyaml",
        ]

        for dep in required_dev_deps:
            assert dep.lower() in content.lower(), (
                f"pyproject.toml must declare dev dependency: {dep}"
            )


class TestGitignorePatterns:
    """Verify .gitignore has required patterns (Requirement 8.2)."""

    def test_gitignore_patterns(self):
        """Verify .gitignore contains all required exclusion patterns."""
        content = GITIGNORE_PATH.read_text()

        required_patterns = [
            "target/",
            "__pycache__/",
            ".venv/",
            "*.pyc",
            "attestation-documents/",
            ".hypothesis/",
            ".pytest_cache/",
        ]

        for pattern in required_patterns:
            assert pattern in content, (
                f".gitignore must contain pattern: {pattern}"
            )
