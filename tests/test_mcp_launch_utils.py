"""Tests for src/mcp/launch_utils.py — launcher selection + GCP cred materialization."""
import json
import os

from src.mcp.launch_utils import materialize_gcp_credentials, resolve_launch_command


def test_explicit_pypi_prefix_routes_to_uvx():
    assert resolve_launch_command("pypi:mcp-server-bigquery", ["--project", "x"]) == (
        "uvx", ["mcp-server-bigquery", "--project", "x"]
    )
    assert resolve_launch_command("uvx:some-pkg", []) == ("uvx", ["some-pkg"])


def test_explicit_npm_prefix_routes_to_npx():
    assert resolve_launch_command("npm:@foo/bar", []) == ("npx", ["-y", "@foo/bar"])
    assert resolve_launch_command("npx:pkg", ["a"]) == ("npx", ["-y", "pkg", "a"])


def test_heuristic_is_backward_compatible():
    # scoped + mcp-server-* still default to npm; bare names to uvx (unchanged).
    assert resolve_launch_command("@modelcontextprotocol/server-x", []) == (
        "npx", ["-y", "@modelcontextprotocol/server-x"]
    )
    assert resolve_launch_command("mcp-server-bigquery", []) == ("npx", ["-y", "mcp-server-bigquery"])
    assert resolve_launch_command("mcp-atlassian", []) == ("uvx", ["mcp-atlassian"])


def _sa_json():
    return json.dumps({
        "type": "service_account", "project_id": "p",
        "private_key": "-----BEGIN PRIVATE KEY-----\nk\n-----END PRIVATE KEY-----\n",
        "client_email": "a@p.iam.gserviceaccount.com",
    })


def test_materialize_writes_key_file_and_points_adc():
    env = {"GOOGLE_APPLICATION_CREDENTIALS_JSON": _sa_json(), "GOOGLE_CLOUD_PROJECT": "p"}
    out = materialize_gcp_credentials(dict(env))
    path = out["GOOGLE_APPLICATION_CREDENTIALS"]
    try:
        assert os.path.isfile(path)
        assert json.load(open(path))["type"] == "service_account"
        assert oct(os.stat(path).st_mode)[-3:] == "600"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_materialize_detects_sa_json_in_any_value():
    out = materialize_gcp_credentials({"SOME_KEY": _sa_json()})
    path = out.get("GOOGLE_APPLICATION_CREDENTIALS")
    assert path and os.path.isfile(path)
    os.remove(path)


def test_materialize_is_noop_without_sa_and_when_file_already_set(tmp_path):
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in materialize_gcp_credentials({"FOO": "bar"})
    existing = tmp_path / "key.json"
    existing.write_text(_sa_json())
    env = {"GOOGLE_APPLICATION_CREDENTIALS": str(existing),
           "GOOGLE_APPLICATION_CREDENTIALS_JSON": _sa_json()}
    out = materialize_gcp_credentials(dict(env))
    assert out["GOOGLE_APPLICATION_CREDENTIALS"] == str(existing)  # respected, not overwritten
