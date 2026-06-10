"""Agent execution environments — Kubernetes pods the agent execs into.

An *environment* is a pod spawned from a Docker image and bound 1:1 to an agent.
The agent's `env__exec`/`bash` tool routes through the kernel (`env.exec` verb);
on allow, the command runs inside the pod via `kubectl exec` and the output is
returned as the tool result. The pod is the sandbox boundary.

Transport is `kubectl` (subprocess) against a configurable context/namespace:
  * ``FORGEOS_KUBE_CONTEXT``   — kube context (default: current context)
  * ``FORGEOS_ENV_NAMESPACE``  — namespace for env pods (default: forgeos-envs)
  * ``FORGEOS_KUBECTL``        — kubectl binary (default: "kubectl")

Bindings persist in the ``agent_environments`` table (migration 015) so they
survive restarts and the kernel can verify ownership; falls back to in-memory
when no DB is wired.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 32_000  # bytes per stream, mirrors dev_tools


def _sanitize(s: str) -> str:
    """k8s name-safe: lowercase alnum + '-', max ~40 chars."""
    out = re.sub(r"[^a-z0-9-]", "-", (s or "").lower()).strip("-")
    return out[:40] or "x"


@dataclass
class EnvBinding:
    env_id: str
    agent_id: str
    image: str
    namespace: str
    pod_name: str
    status: str = "pending"


class EnvironmentManager:
    """Spawns/execs/tears-down per-agent environment pods via kubectl."""

    def __init__(self, db_client: Any = None, *, tenant_id: str = "default",
                 namespace: str | None = None, context: str | None = None) -> None:
        self._db = db_client
        self._tenant_id = tenant_id
        self._namespace = namespace or os.environ.get("FORGEOS_ENV_NAMESPACE", "forgeos-envs")
        self._context = context or os.environ.get("FORGEOS_KUBE_CONTEXT") or None
        self._kubectl = os.environ.get("FORGEOS_KUBECTL", "kubectl")
        self._mem: dict[str, EnvBinding] = {}  # agent_id -> binding (cache / no-DB fallback)
        self._materialize_kubeconfig()

    @staticmethod
    def _materialize_kubeconfig() -> None:
        """When running on Cloud Run (no kubeconfig on disk), write the one
        provided via FORGEOS_KUBECONFIG_CONTENT to a temp file and point
        KUBECONFIG at it. The kubeconfig carries no credentials — it relies on
        the gke-gcloud-auth-plugin using the container's ADC identity. No-op
        when KUBECONFIG is already set (local/kind) or the var is absent."""
        content = os.environ.get("FORGEOS_KUBECONFIG_CONTENT")
        if not content or os.environ.get("KUBECONFIG"):
            return
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "forgeos-kubeconfig.yaml")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            os.environ["KUBECONFIG"] = path
            logger.info("EnvironmentManager: materialized kubeconfig at %s", path)
        except OSError:
            logger.exception("EnvironmentManager: failed to write kubeconfig")

    # -- kubectl plumbing -----------------------------------------------------

    def _kc(self, *args: str) -> list[str]:
        cmd = [self._kubectl]
        if self._context:
            cmd += ["--context", self._context]
        return cmd + list(args)

    def _run(self, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout[:_MAX_OUTPUT], p.stderr[:_MAX_OUTPUT]
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout}s"
        except FileNotFoundError:
            return 127, "", f"{self._kubectl} not found on PATH"
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)

    def _ensure_namespace(self) -> None:
        rc, _, _ = self._run(self._kc("get", "ns", self._namespace), timeout=15)
        if rc != 0:
            self._run(self._kc("create", "ns", self._namespace), timeout=20)

    # -- persistence ----------------------------------------------------------

    def _save(self, b: EnvBinding) -> None:
        self._mem[b.agent_id] = b
        if not (self._db and getattr(self._db, "is_connected", False)):
            return
        try:
            with self._db.tenant(self._tenant_id) as conn:
                conn.execute(
                    "INSERT INTO agent_environments "
                    "(env_id, tenant_id, agent_id, image, namespace, pod_name, status, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s, NOW()) "
                    "ON CONFLICT (tenant_id, agent_id) DO UPDATE SET "
                    "env_id=EXCLUDED.env_id, image=EXCLUDED.image, namespace=EXCLUDED.namespace, "
                    "pod_name=EXCLUDED.pod_name, status=EXCLUDED.status, updated_at=NOW()",
                    (b.env_id, self._tenant_id, b.agent_id, b.image, b.namespace, b.pod_name, b.status),
                )
                conn.commit()
        except Exception:
            logger.exception("EnvironmentManager: persist failed for %s", b.agent_id)

    def binding(self, agent_id: str) -> EnvBinding | None:
        if agent_id in self._mem:
            b = self._mem[agent_id]
            return b if b.status != "deleted" else None
        if self._db and getattr(self._db, "is_connected", False):
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    row = conn.execute_one(
                        "SELECT env_id, agent_id, image, namespace, pod_name, status "
                        "FROM agent_environments WHERE tenant_id=%s AND agent_id=%s AND status!='deleted'",
                        (self._tenant_id, agent_id),
                    )
                if row:
                    b = EnvBinding(row["env_id"], row["agent_id"], row["image"],
                                   row["namespace"], row["pod_name"], row["status"])
                    self._mem[agent_id] = b
                    return b
            except Exception:
                logger.debug("EnvironmentManager: binding lookup failed", exc_info=True)
        return None

    def bound_env_id(self, agent_id: str) -> str | None:
        b = self.binding(agent_id)
        return b.env_id if b else None

    # -- lifecycle ------------------------------------------------------------

    async def spawn(self, agent_id: str, image: str) -> EnvBinding:
        """Create (or reuse) the agent's environment pod and wait until Ready."""
        existing = self.binding(agent_id)
        if existing and existing.image == image and existing.status == "running":
            return existing
        env_id = (existing.env_id if existing else f"env-{uuid.uuid4().hex[:12]}")
        pod = f"forgeos-env-{_sanitize(agent_id)}-{env_id.split('-')[-1]}"
        b = EnvBinding(env_id, agent_id, image, self._namespace, pod, status="pending")
        self._save(b)

        def _spawn_sync() -> EnvBinding:
            self._ensure_namespace()
            # Recreate cleanly (idempotent): drop any prior pod for this env.
            self._run(self._kc("delete", "pod", pod, "-n", self._namespace, "--ignore-not-found"), timeout=30)
            rc, _, err = self._run(self._kc(
                "run", pod, "--image", image, "--restart", "Never", "-n", self._namespace,
                "--labels", f"forgeos.env={env_id},forgeos.agent={_sanitize(agent_id)},app=forgeos-env",
                "--command", "--", "sh", "-c", "sleep infinity",
            ), timeout=60)
            if rc != 0:
                b.status, b.last_error = "failed", err  # type: ignore[attr-defined]
                self._save(b)
                logger.error("env spawn failed for %s: %s", agent_id, err)
                return b
            rc2, _, err2 = self._run(self._kc(
                "wait", f"pod/{pod}", "--for=condition=Ready", "-n", self._namespace, "--timeout=90s",
            ), timeout=100)
            b.status = "running" if rc2 == 0 else "failed"
            self._save(b)
            if rc2 != 0:
                logger.error("env pod not ready for %s: %s", agent_id, err2)
            else:
                logger.info("env pod ready: %s (%s) for agent %s", pod, image, agent_id)
            return b

        return await asyncio.to_thread(_spawn_sync)

    def exec_sync(self, agent_id: str, command: str, timeout: int = 120) -> dict[str, Any]:
        """Synchronously run a command in the agent's env pod (kubectl exec).

        Used as the kernel `env.exec` dispatcher (the syscall pipeline is sync).
        Admission is the caller's responsibility; this only executes."""
        b = self.binding(agent_id)
        if not b:
            return {"ok": False, "stdout": "", "stderr": "no environment bound", "code": -1}
        if b.status != "running":
            return {"ok": False, "stdout": "", "stderr": f"environment not running ({b.status})", "code": -1}
        rc, out, err = self._run(self._kc(
            "exec", b.pod_name, "-n", b.namespace, "--", "sh", "-c", command,
        ), timeout=timeout)
        return {"ok": rc == 0, "stdout": out, "stderr": err, "code": rc}

    async def exec(self, agent_id: str, command: str, timeout: int = 120) -> dict[str, Any]:
        """Async wrapper around :meth:`exec_sync` (lazy-respawns if the pod was lost)."""
        b = self.binding(agent_id)
        if b and b.status != "running":
            await self.spawn(agent_id, b.image)
        return await asyncio.to_thread(self.exec_sync, agent_id, command, timeout)

    async def teardown(self, agent_id: str) -> bool:
        b = self.binding(agent_id)
        if not b:
            return False

        def _del_sync() -> None:
            self._run(self._kc("delete", "pod", "-l", f"forgeos.env={b.env_id}",
                               "-n", b.namespace, "--ignore-not-found"), timeout=30)

        await asyncio.to_thread(_del_sync)
        b.status = "deleted"
        self._save(b)
        self._mem.pop(agent_id, None)
        return True
