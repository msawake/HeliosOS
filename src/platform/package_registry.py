"""
Content-addressed agent package registry.

Phase 2 #1. Today an agent's ``metadata.version`` is decorative — nothing
ties a running agent to a reproducible artifact, so rollback is not a
primitive. This module makes the artifact real: the resolved manifest
(plus, optionally, a bundle of code/prompt files) hashes to a stable
SHA-256, which becomes the agent's content id. Deploying by digest is
reproducible; rolling back is ``deploy(old_digest)``.

Scope of this module:
  * Deterministic hashing of a manifest dict + (optional) directory of
    files. Byte-for-byte stable across platforms.
  * A filesystem-backed registry under ``~/.forgeos/packages/`` by
    default (override via constructor). Stores one directory per digest
    with ``manifest.json`` + a ``files/`` tree.
  * ``name@version`` resolution via an index file that maps
    ``namespace/name`` + ``version`` to the digest it was last pushed at.

Non-goals for this session:
  * OCI compatibility. The layout is our own; migration to OCI is
    straightforward later.
  * Remote registries (S3, GCS). Local filesystem first.
  * Signing. Raw SHA-256 is our integrity primitive.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_REGISTRY_ROOT = Path(
    os.environ.get("FORGEOS_PACKAGE_ROOT", Path.home() / ".forgeos" / "packages")
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _canonical_json(obj: Any) -> bytes:
    """Byte-stable JSON: sorted keys, no whitespace, utf-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash_directory(root: Path) -> str:
    """Hash every file under ``root`` in a stable order, including its
    relative path. Returns the hex digest (no ``sha256:`` prefix).

    Missing or empty ``root`` returns the hash of an empty string so the
    two cases are distinguishable from "files present".
    """
    if not root.exists():
        return hashlib.sha256(b"").hexdigest()
    h = hashlib.sha256()
    entries = sorted(
        p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )
    for path in entries:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        data = path.read_bytes()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Package record
# ---------------------------------------------------------------------------


@dataclass
class Package:
    """A resolved agent package — the thing you deploy.

    * ``manifest`` is a dict (typically produced by
      :meth:`AgentManifest.canonical_dict`). It is the authoritative
      source of all runtime config.
    * ``files_dir`` is an optional directory containing prompts,
      scaffolds, or code bundled with the manifest. The registry hashes
      its contents (ignoring ``__pycache__``) into the digest.
    * ``digest`` is a content id of the form ``sha256:...``. Computed by
      :meth:`compute_digest`; do not construct externally.
    * ``metadata`` is registry-side bookkeeping (push time, pushed_by).
    """

    manifest: dict[str, Any]
    files_dir: Path | None = None
    digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compute_digest(cls, manifest: dict[str, Any], files_dir: Path | None) -> str:
        """Stable SHA-256 over manifest + files_dir contents."""
        h = hashlib.sha256()
        h.update(b"manifest:")
        h.update(_canonical_json(manifest))
        h.update(b"|files:")
        h.update(
            (_hash_directory(files_dir) if files_dir else "empty").encode("utf-8")
        )
        return f"sha256:{h.hexdigest()}"

    def finalize(self) -> str:
        """Compute and set the digest. Returns it."""
        self.digest = self.compute_digest(self.manifest, self.files_dir)
        return self.digest

    @property
    def name(self) -> str:
        return (self.manifest.get("metadata") or {}).get("name", "")

    @property
    def namespace(self) -> str:
        return (self.manifest.get("metadata") or {}).get("namespace", "default")

    @property
    def version(self) -> str:
        return (self.manifest.get("metadata") or {}).get("version", "0.0.0")

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}"


# ---------------------------------------------------------------------------
# Registry errors
# ---------------------------------------------------------------------------


class PackageNotFound(LookupError):
    """Raised when pull/resolve cannot find the requested digest or name@version."""


class InvalidDigest(ValueError):
    """Raised when a supplied string is not a valid ``sha256:...`` digest."""


# ---------------------------------------------------------------------------
# Filesystem-backed registry
# ---------------------------------------------------------------------------


class FilesystemPackageRegistry:
    """On-disk package registry.

    Layout::

        <root>/
            index.json                  # name@version -> digest
            <digest>/
                manifest.json
                files/                  # optional; mirrors Package.files_dir
                meta.json               # push time, pushed_by, etc.

    Pushes are atomic (write-then-rename) so a crashed push never leaves
    a half-written directory visible. ``list`` / ``resolve`` rely on the
    index; ``pull_by_digest`` walks the directory directly so a missing
    or corrupt index does not lose content.
    """

    def __init__(self, root: Path | str = DEFAULT_REGISTRY_ROOT) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        if not self._index_path.exists():
            self._write_index({})

    # -- index helpers -----------------------------------------------------

    def _read_index(self) -> dict[str, dict]:
        try:
            return json.loads(self._index_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_index(self, data: dict[str, dict]) -> None:
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._index_path)

    @staticmethod
    def _index_key(namespace: str, name: str, version: str) -> str:
        return f"{namespace}/{name}@{version}"

    # -- core operations ---------------------------------------------------

    def push(self, package: Package, *, pushed_by: str = "") -> str:
        """Store ``package`` and return its digest.

        Duplicate pushes are idempotent: if the digest already exists,
        the on-disk payload is left alone and the index entry is
        refreshed to point at the existing directory.

        Rejects packages whose ``metadata.version`` is not semver —
        ``metadata.version`` is load-bearing once we have a registry.
        """
        if not package.digest:
            package.finalize()

        version = package.version
        if not _SEMVER_RE.match(version):
            raise ValueError(
                f"metadata.version must be semver (got {version!r}) — "
                "package registry requires pinnable versions"
            )

        target = self.root / package.digest.replace("sha256:", "sha256_")
        if not target.exists():
            staging = target.with_name(target.name + ".staging")
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            (staging / "manifest.json").write_text(
                json.dumps(package.manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
            if package.files_dir and package.files_dir.exists():
                shutil.copytree(package.files_dir, staging / "files")
            meta = {
                "digest": package.digest,
                "namespace": package.namespace,
                "name": package.name,
                "version": package.version,
                "pushed_at": datetime.now(timezone.utc).isoformat(),
                "pushed_by": pushed_by,
                **package.metadata,
            }
            (staging / "meta.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
            )
            staging.rename(target)

        # Update the index (latest push wins for this name@version).
        index = self._read_index()
        index[self._index_key(package.namespace, package.name, package.version)] = {
            "digest": package.digest,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_index(index)
        return package.digest

    def pull(self, digest: str) -> Package:
        """Return a ``Package`` reconstructed from on-disk state."""
        if not _DIGEST_RE.match(digest):
            raise InvalidDigest(f"not a valid sha256 digest: {digest!r}")
        target = self.root / digest.replace("sha256:", "sha256_")
        manifest_path = target / "manifest.json"
        if not manifest_path.exists():
            raise PackageNotFound(f"digest {digest} not in registry at {self.root}")
        manifest = json.loads(manifest_path.read_text("utf-8"))
        files_dir = target / "files"
        meta: dict[str, Any] = {}
        meta_path = target / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
            except json.JSONDecodeError:
                pass
        package = Package(
            manifest=manifest,
            files_dir=files_dir if files_dir.exists() else None,
            digest=digest,
            metadata=meta,
        )
        return package

    def resolve(self, namespace: str, name: str, version: str) -> str:
        """Return the digest currently pinned to ``namespace/name@version``."""
        index = self._read_index()
        entry = index.get(self._index_key(namespace, name, version))
        if entry is None:
            raise PackageNotFound(
                f"{namespace}/{name}@{version} not in registry index"
            )
        return str(entry["digest"])

    def list(
        self,
        *,
        namespace: str | None = None,
        name: str | None = None,
    ) -> list[dict]:
        """List index entries (optionally filtered by namespace/name)."""
        index = self._read_index()
        out: list[dict] = []
        for key, entry in sorted(index.items()):
            ns, rest = key.split("/", 1)
            n, ver = rest.rsplit("@", 1)
            if namespace is not None and ns != namespace:
                continue
            if name is not None and n != name:
                continue
            out.append({
                "namespace": ns, "name": n, "version": ver, **entry,
            })
        return out

    def list_digests(self) -> list[str]:
        """Walk the registry root and return every present digest directory."""
        digests: list[str] = []
        for child in self.root.iterdir():
            if child.is_dir() and child.name.startswith("sha256_"):
                digests.append(child.name.replace("sha256_", "sha256:"))
        return sorted(digests)


__all__ = [
    "DEFAULT_REGISTRY_ROOT",
    "FilesystemPackageRegistry",
    "InvalidDigest",
    "Package",
    "PackageNotFound",
]
