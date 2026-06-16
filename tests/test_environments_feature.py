"""Tests for the reusable-environments feature:

- PostgresEnvDefStore in-memory fallback (no DB wired)
- EnvironmentService attach/detach pointer + spawn/teardown
- pod_dev_tools.run_in_pod tool→pod-command mapping
- ToolExecutor redirect branch (pod when bound+running, local otherwise)
"""

import pytest

from stacks.base import AgentDefinition, ExecutionType, OwnershipType
from src.platform.environments import EnvBinding
from src.platform.env_service import EnvironmentService
from src.platform.persistence import PostgresEnvDefStore
from src.platform.registry import AgentRegistry
from src.platform import pod_dev_tools


# --- PostgresEnvDefStore (in-memory fallback) -------------------------------

@pytest.fixture
def def_store():
    # No db_client → graceful in-memory fallback.
    return PostgresEnvDefStore(db_client=None, tenant_id="t1")


def test_env_def_create_get_list(def_store):
    d = def_store.create(name="py", image="python:3.12", env_vars={"A": "1"}, resources={"cpu": "500m"})
    assert d.env_def_id.startswith("envdef-")
    assert def_store.get(d.env_def_id).image == "python:3.12"
    assert def_store.get_by_name("py").env_def_id == d.env_def_id
    assert len(def_store.list()) == 1


def test_env_def_update_and_delete(def_store):
    d = def_store.create(name="py", image="python:3.12")
    def_store.update(d.env_def_id, image="python:3.13", env_vars={"X": "y"})
    assert def_store.get(d.env_def_id).image == "python:3.13"
    assert def_store.get(d.env_def_id).env_vars == {"X": "y"}
    assert def_store.delete(d.env_def_id) is True
    assert def_store.get(d.env_def_id) is None


# --- EnvironmentService -----------------------------------------------------

class _FakeEnvMgr:
    """Records spawn/teardown calls; returns a running binding."""

    def __init__(self):
        self.spawned = []
        self.torn_down = []
        self._bindings = {}

    async def spawn(self, agent_id, image, *, env_vars=None, resources=None, env_def_id=None):
        self.spawned.append((agent_id, image, env_vars, resources, env_def_id))
        b = EnvBinding(env_id="env-x", agent_id=agent_id, image=image, namespace="ns",
                       pod_name="pod-x", status="running", env_def_id=env_def_id)
        self._bindings[agent_id] = b
        return b

    async def teardown(self, agent_id):
        self.torn_down.append(agent_id)
        self._bindings.pop(agent_id, None)
        return True

    def binding(self, agent_id):
        return self._bindings.get(agent_id)


def _agent(agent_id="a1"):
    return AgentDefinition(
        name="bot", stack="forgeos", execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED, agent_id=agent_id, metadata={},
    )


async def test_service_attach_sets_pointer_and_spawns():
    reg = AgentRegistry()
    agent = _agent()
    reg.register(agent)
    store = PostgresEnvDefStore(db_client=None, tenant_id="t1")
    d = store.create(name="py", image="python:3.12", env_vars={"A": "1"})
    mgr = _FakeEnvMgr()
    svc = EnvironmentService(env_def_store=store, registry=reg, env_mgr=mgr)

    res = await svc.attach("a1", d.env_def_id)

    assert res["ok"] is True
    assert reg.get("a1").metadata["_env_def_id"] == d.env_def_id
    assert mgr.spawned == [("a1", "python:3.12", {"A": "1"}, {}, d.env_def_id)]
    assert svc.agents_using(d.env_def_id) == ["a1"]


async def test_service_detach_clears_pointer_and_tears_down():
    reg = AgentRegistry()
    reg.register(_agent())
    store = PostgresEnvDefStore(db_client=None, tenant_id="t1")
    d = store.create(name="py", image="python:3.12")
    mgr = _FakeEnvMgr()
    svc = EnvironmentService(env_def_store=store, registry=reg, env_mgr=mgr)
    await svc.attach("a1", d.env_def_id)

    res = await svc.detach("a1")

    assert res["ok"] is True
    assert "_env_def_id" not in reg.get("a1").metadata
    assert mgr.torn_down == ["a1"]


async def test_service_delete_def_refuses_while_attached():
    reg = AgentRegistry()
    reg.register(_agent())
    store = PostgresEnvDefStore(db_client=None, tenant_id="t1")
    d = store.create(name="py", image="python:3.12")
    mgr = _FakeEnvMgr()
    svc = EnvironmentService(env_def_store=store, registry=reg, env_mgr=mgr)
    await svc.attach("a1", d.env_def_id)

    res = svc.delete_def(d.env_def_id)
    assert res["ok"] is False
    assert res["agents"] == ["a1"]
    assert store.get(d.env_def_id) is not None  # not deleted

    await svc.detach("a1")
    assert svc.delete_def(d.env_def_id)["ok"] is True


# --- pod_dev_tools.run_in_pod mapping --------------------------------------

class _RecordingEnvMgr:
    """Captures the command/stdin/env passed to exec()."""

    def __init__(self, ok=True, stdout="", stderr="", code=0):
        self.calls = []
        self._ret = {"ok": ok, "stdout": stdout, "stderr": stderr, "code": code}

    async def exec(self, agent_id, command, *, stdin=None, env=None, timeout=120):
        self.calls.append({"command": command, "stdin": stdin, "env": env, "timeout": timeout})
        return dict(self._ret)


async def test_pod_shell_exec_allowlist_and_cwd():
    mgr = _RecordingEnvMgr(stdout="hi", code=0)
    out = await pod_dev_tools.run_in_pod(
        mgr, "a1", "shell__exec", {"cmd": "ls -la", "cwd": "/work"}, {"invocation_id": "inv1"}
    )
    assert out["success"] is True
    assert out["result"]["returncode"] == 0
    assert out["result"]["stdout"] == "hi"
    cmd = mgr.calls[0]["command"]
    assert "cd /work" in cmd and "ls -la" in cmd


async def test_pod_shell_exec_rejects_unlisted_binary():
    mgr = _RecordingEnvMgr()
    out = await pod_dev_tools.run_in_pod(mgr, "a1", "shell__exec", {"cmd": "rm -rf /"}, {})
    assert out["result"]["ok"] is False
    assert "allowlist" in out["result"]["error"]
    assert mgr.calls == []  # never executed


async def test_pod_fs_write_file_streams_base64_via_stdin():
    mgr = _RecordingEnvMgr(code=0)
    out = await pod_dev_tools.run_in_pod(
        mgr, "a1", "fs__write_file", {"path": "/tmp/x.txt", "content": "hello"}, {}
    )
    assert out["result"]["ok"] is True
    assert out["result"]["bytes_written"] == 5
    call = mgr.calls[0]
    assert "base64 -d > /tmp/x.txt" in call["command"]
    # stdin is base64("hello")
    import base64
    assert call["stdin"] == base64.b64encode(b"hello").decode()


async def test_pod_gh_open_pr_requires_token():
    mgr = _RecordingEnvMgr()
    out = await pod_dev_tools.run_in_pod(
        mgr, "a1", "gh__open_pr",
        {"repo_dir": "/r", "branch": "b", "title": "t", "body": "x"}, {}
    )
    assert out["result"]["ok"] is False
    assert "GH_TOKEN" in out["result"]["error"]
    assert mgr.calls == []


async def test_pod_git_commit_push_injects_token_env():
    mgr = _RecordingEnvMgr(code=0)
    ctx = {"_credentials": {"gh_token": "ghp_secret"}}
    out = await pod_dev_tools.run_in_pod(
        mgr, "a1", "git__commit_push",
        {"repo_dir": "/r", "branch": "feat", "message": "msg", "files": ["."]}, ctx
    )
    assert out["result"]["ok"] is True
    call = mgr.calls[0]
    assert call["env"]["GH_TOKEN"] == "ghp_secret"
    assert "credential.helper" in call["command"]
