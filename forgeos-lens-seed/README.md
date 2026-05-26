# ForgeOS Lens

A desktop client for ForgeOS — like OpenLens for Kubernetes. It wraps the
`forgeos` CLI in a Tauri shell and surfaces agents, runs, approvals, and
logs in a UI you can actually navigate.

This repository is scaffolded and iterated by the **forgeos-lens-builder**
agent (running on Nemotron-3-Super via vLLM on `rtx3`), which:

  1. Reads `dashboard/spec.md` to decide what to build next.
  2. Asks the human (via A2H) freeform questions when something is unclear
     (e.g. package manager, navigation grouping).
  3. Drives `opencode` to write the code.
  4. Runs `pnpm build` / `cargo check` until green.
  5. Commits to a feature branch and opens a PR for human review.

See `dashboard/spec.md` for what the UI should look like and which `forgeos`
CLI calls it depends on.

## Local dev (target state — agent will scaffold)

```bash
pnpm install
pnpm tauri dev
```

## Status

Bootstrapping. The first PR from the agent should land the Tauri shell + a
read-only agents list view.
