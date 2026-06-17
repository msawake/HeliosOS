"""Helios OS Pulumi entrypoint — dual-target dispatcher.

One program, two targets, selected by `forgeos:target`:
  - target=gcp   (default) → gcp_stack.py: GKE Autopilot + Cloud Run + Cloud SQL
  - target=local           → local_stack.py: per-agent pods on a local k8s cluster

Each target's program is a module whose top-level code builds the stack; importing
it runs it. Keeping them in separate modules lets the GCP path stay byte-for-byte
what it was while the local path evolves independently.
"""
import pulumi

_target = (pulumi.Config().get("target") or "gcp").lower()

if _target == "local":
    import local_stack  # noqa: F401  (top-level code builds the local stack)
else:
    import gcp_stack  # noqa: F401  (top-level code builds the GCP stack)
