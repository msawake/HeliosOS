# Securing Agents & MCPs — Proposal

**Status:** Step 1 (problem definition) — COMPLETE · Step 2 (solutions) — REVISED post security review (see §13) + compute topology (§11.7) · Step 3 (draw.io) — DONE → 3 diagrams (architecture, analogy, compute-topology)
**Date:** 2026-05-28
**Owner:** antoni.bergas@makingscience.com
**Scope:** ForgeOS agent/MCP authorization. Driven by two concrete needs: a multi-project read-only GCP auditor, and multi-user Google Workspace access with permission layers.

---

## 1. Thesis

ForgeOS *attempts* per-user credential isolation for GitHub: a PAT is stored per user (`forgeos-github-pat-{user_id}`), write-only, no read-back (`src/platform/credentials.py`). **But the security review found this path is broken and leaky** (§13.1): the injected credential is **serialized into the LLM prompt** (`agentic_loop.py:141`) and does **not** reach tools via the documented `agent_context` (`build_agent_context` omits `_credentials`). So even the one integration we believed was right must be **fixed before** it can be the template the broker generalizes.

**GCP and Google Workspace do not follow it.** Both run on a **single ambient platform identity**, shared by every agent and every user, with "read-only / one-project / one-mailbox" enforced by **prompt text, not the credential or IAM boundary.**

The two drivers — *audit all projects* and *share Workspace across users with permission layers* — are the **same root problem**: ambient shared authority + authorization-by-prompt. Naively widening scope multiplies blast radius instead of containing it.

## 2. Drivers

| | Today | Wanted | Latent risk |
|---|---|---|---|
| GCP audit | `sre-gcp-auditor` reads **one** project (`admachina-atomic-test-84`) | One agent that audits **all** projects | Single shared SA needs org-wide read → any agent can read the whole estate |
| Workspace MCP | Sends/reads as **antoni's** Google account via one OAuth refresh token | Multiple users, **layered permissions** | One token = one identity for all: no attribution, no per-user scope, single point of compromise |

## 3. Current-state ground truth (evidence-backed)

**Credential plane**

| Resource | Identity | Scoping | Secret | Authorized by |
|---|---|---|---|---|
| GitHub | Per-user PAT | **Per user** (`owner_id` → `forgeos-github-pat-{user_id}`) | Secret Manager, write-only | ⚠️ **leaks to LLM prompt**; doesn't reach tools (§13.1) |
| Google Workspace | **One** human account (antoni's refresh token) | **None** — `_handle_email`/`_handle_drive_audit` ignore `agent_context` | Single global `FORGEOS_GWS_*` | Prompt only ⚠️ |
| GCP (`gcloud`/Vertex) | **One** Cloud Run SA via ambient ADC | **None** — every agent in the container inherits it | Metadata server (no per-agent identity) | Prompt only (`--project=`) ⚠️ |

**Control plane**

- **Auth = single shared token.** `check_auth` accepts one bearer/API key; `AuthUser`/RBAC (`src/api/auth.py`) defined but **unwired**. GET read-prefixes open. Cloud Run IaC grants `run.invoker` to `allUsers` (public ingress) → the app's shared token is the only gate.
- **Tenancy isolates rows, not creds/compute.** `tenant_id` drives Postgres RLS but is set once at boot; `admin()` connections bypass it; it does nothing to secrets, the SA, or the shared process.
- **No per-agent isolation.** All `stack: forgeos` agents are async tasks in **one** Cloud Run container. Sandbox/Docker path falls back (no daemon on Cloud Run); k8s manifests are for the platform, not per-agent. "Agent credentials" = "container credentials."
- **Enforcement uncertain.** Kernel/syscall pipeline has the right shape, but Community-Edition kernel stubs return `allow()` for everything, and flag defaults are ambiguous. **Must be verified on the live deployment, not assumed.**
- **IaC drift.** The audit works today only because `roles/viewer` was granted to the Cloud Run SA **out-of-band** (manual `gcloud`), not in Terraform/Pulumi (which grant only narrow functional roles).

## 4. Core problem (root cause)

> ForgeOS grants cloud/SaaS access through a single, ambient, shared identity, constrained by prompts rather than the identity/permission boundary. Per-user/per-agent least privilege exists only for GitHub. Widening GCP scope (all projects) or Workspace usage (all users) on this model expands one blast radius instead of partitioning it.

## 5. Threat model

1. **Agent drift / prompt injection** — "read-only / project X only" are prompt promises; the IAM/OAuth boundary would allow more.
2. **Secret-compromise blast radius** — one leaked `FORGEOS_GWS_REFRESH_TOKEN` = full Gmail/Drive for everyone; one over-scoped SA = whole estate.
3. **No attribution** — many users' agents acting as one identity can't answer "which user's agent did this?".
4. **Cross-user / cross-tenant leakage** — nothing at the credential/compute layer separates one user's or team's authority from another's.
5. **Over-broad standing privilege** — ambient ADC is always-on; no per-task, time-boxed, revocable grant.

## 6. Problem decomposition

| # | Sub-problem | Risk | Worsened by widening |
|---|---|---|---|
| P1 | Ambient GCP authority; no per-agent GCP identity | Confused deputy | "All projects" → org-wide read on one identity |
| P2 | Single shared human Workspace identity | No attribution, over-scope, single secret | "Many users" → everyone is antoni |
| P3 | Authorization by prompt, not boundary | Read-only unenforced | More scope behind a prompt promise |
| P4 | Weak platform authn/z (shared token, RBAC unwired, public ingress) | One token = full control plane | More users sharing one token |
| P5 | No isolation at compute/credential layer | All agents share one process & creds | More agents/tenants in one blast radius |
| P6 | Enforcement uncertainty + IaC drift | Controls may be off; access undocumented | Hard to scale safely |

## 7. Decisions (locked, 2026-05-28)

1. **Audit reach = explicit allowlist, IAM-enforced.** The auditor identity is granted read **only** on listed projects/folders. Excluded = no grant = unreadable (**hard** exclude). A soft include/exclude config rides on top for per-run *targeting* only.
2. **Identity model = on-behalf-of (OBO).** A user-*launched* agent runs **as the launching user**, resolving that user's **own** per-integration credentials (GitHub PAT today; Workspace OAuth + GCP to add). The run is visible **only to that user**. The agent **preflight-requires** the user-level integrations it needs — **no silent fallback to a shared/`default` identity**.
3. **Trust boundary = multiple internal teams, isolated.** Namespaces become **enforced** boundaries (own creds + data), not labels.

## 8. Target model — two identity planes

| Plane | Applies to | Identity | Bounded by |
|---|---|---|---|
| **On-behalf-of (OBO)** | User-invoked / interactive agents | The **launching user's** own creds | The user's own IAM/OAuth scopes (automatic least privilege + attribution) |
| **Service identity** | **Scheduled / autonomous** agents (auditors) | A **dedicated, narrowly-scoped SA** per job | The IAM **allowlist** (decision #1) |

**Keystone tension resolved:** the `sre-gcp-auditor` is scheduled → no launching user → it belongs to the **service-identity** plane (a dedicated audit SA holding the allowlist), **not** OBO. OBO applies to interactive agents.

## 9. Prerequisites this creates (foundation for Step 2)

- **Wire real per-user authentication** at the edge (replace the single shared bearer token; light up `AuthUser`/RBAC). OBO is impossible without a caller identity.
- **Remove the `default` credential fallback**; replace with **required-integration preflight** (fail fast + actionable message when a user-level integration isn't configured).
- **Make namespaces enforced** for credentials, run visibility, and data — not query filters. (Leverage existing per-user MCP plumbing in `client_mcp_manager.py`.)
- **Verify/encode enforcement**: confirm the kernel/policy path is actually live; express read-only + scope as **policy**, not prompt.

## 10. Success criteria

Least privilege provable by IAM/OAuth (not prompt) · read-only enforced by the role · per-user/per-agent identity + attribution in the hash-chained audit trail · per-team blast-radius containment · revocable, ideally time-boxed grants · **no secret read-back** (keep write-only model) · configurable scope with **IAM-enforced (hard) exclusion**, not merely config · scalable to N projects and N users without N hand-rotated secrets · run visibility scoped to the launching user / their team.

## 11. Step 2 — Solution options & recommendation

### 11.0 Keystone component — the Credential Broker

The single mechanism that converts today's *ambient shared authority* into *per-run, per-identity, scoped, short-lived, policy-gated, audited* credentials. Agents never hold standing cloud/SaaS creds. At invocation the broker:

1. **Identifies the principal** — the **launching user** (OBO) or the agent's **service principal** (scheduled).
2. **Policy-checks** whether that principal + agent may obtain the requested target credential (kernel capability/policy).
3. **Mints a short-lived, narrowly-scoped** credential — SA-impersonation token, OAuth access token, or PAT — for exactly that target.
4. **Injects** it into `invoke_ctx["_credentials"]` for that run only — never `os.environ`, never persisted (extends the existing GitHub-PAT pattern).
5. **Audits** the mint (principal, agent, namespace, target, scope, TTL) in the hash-chained log.

This chokepoint makes least privilege *enforceable* and attribution *complete*. **Critical constraint:** the impersonation/mint permissions live on a dedicated **broker SA used only by the broker code path** — NOT on the general runtime SA, and agents must NOT be able to self-impersonate via `shell__exec gcloud --impersonate-service-account` (see §11.6).

### 11.1 Axis A — Auditor hosting topology

| Option | Blast radius | Least priv | Ops cost | Auditor integrity | Verdict |
|---|---|---|---|---|---|
| A1 ForgeOS in every project | 1 proj/instance | High | **Very high** (N deploys/upgrades/secrets) | Med | Over-isolates; fragments reporting; not "one agent" |
| A2 Central + single audit SA on allowlist | **entire allowlist** | Med | Low | Med | Simple but one SA reads everything |
| **A3 Central + per-project read-only SA, broker-impersonated** | **1 proj / credential** | **High** | Med (automatable via TF) | Med | **Recommended** — per-project least priv + central control; hard-exclude = no SA |
| A4 External / out-of-org host | per-proj (w/ A3) | High | High | **High** (tamper-resistant) | Defense-in-depth layer on A3 for sensitive estates |

**Recommendation: A3** — per-project read-only SAs minted by the broker via policy-gated impersonation, with **folder-level grants** where the allowlist is folder-shaped (auto-covers new projects in included folders). **Hard-exclude** (decision #1) = no SA / no grant / no impersonation binding for that project. Layer **A4** (external host) later when auditing prod/security-sensitive projects where the audited party must not be able to tamper with the auditor.

### 11.2 Axis B — OBO realization per integration

| Integration | Mechanism | Notes |
|---|---|---|
| GitHub | **Per-user PAT** (exists) | Generalize the vault; keep write-only + per-invocation inject |
| Workspace | **Per-user OAuth** refresh token | One-time offline consent; store `forgeos-oauth-gws-{user_id}`; acts as the user, scoped to consented scopes ("layers" = scopes + Workspace role); revoke = delete token / user revokes in Google |
| GCP (OBO) | **Per-user SA impersonation** (recommended) | Map user → `forgeos-user-{user_id}` SA granted that user's *agent* scope; broker impersonates → short-lived, keyless, revocable, scope **≤** user. Alt: per-user OAuth (cloud-platform) when "exactly what the human can do" is required; WIF for keyless external mapping |

Scheduled/service agents that must email use a **service mailbox** or narrow domain-wide delegation (service plane), not a user token. All paths flow through the broker.

### 11.3 Axis C — Per-user auth + enforced namespaces (foundation)

- **Per-user auth:** Google Workspace **SSO / OIDC** (natural for internal MS) → short-lived **JWT** carrying `user_id`, email, team/namespace(s), role. Wire into `check_auth` (light up the existing `AuthUser`/RBAC). CLI gets `forgeos login` (OAuth device flow).
- **Kill the `default` fallback:** `user_id` comes from the authenticated caller; OBO agents reject if absent; scheduled agents run as their declared **service principal**, never `"default"`.
- **Namespaces become enforced walls** at three layers: (1) **authz** — caller may only invoke/deploy in namespaces their team allows (kernel capability); (2) **broker** — an agent in ns X cannot resolve ns Y's creds; (3) **data** — runs + audit rows tagged `{user_id, namespace}`; logs/list/describe filter by caller identity/team; **set namespace/tenant per-request from the JWT** (fixes the set-once-at-boot gap), enforced by RLS keyed on namespace + owner.

### 11.4 Axis D — Enforcement

- **Verify the kernel is live** on the deploy (real kernel vs Community stubs that `allow()` everything; confirm `FORGEOS_SYSCALL_PIPELINE`; confirm policies exist). *Precondition — without it, none of the above is enforced.*
- **Encode as policy, not prompt:** auditor read-only = the SA holds only read roles (IAM enforces; optionally a **deny policy** / **VPC-SC** perimeter for hard guarantees); allowed tools + which credentials an agent may mint = capability/policy.
- **Attribution:** every mint + tool call carries `{principal, agent, namespace, target, scope}` in the hash-chained log.

### 11.5 Recommended architecture (the secure way, in one paragraph)

A **central ForgeOS** platform with a **Credential Broker** at its core. **Scheduled auditors** run as service principals that obtain **per-project (or per-included-folder) read-only** credentials via **broker-gated SA impersonation** — hard-exclude = no grant; optionally hosted **externally** for tamper-resistance. **Interactive agents** run **on-behalf-of the launching user**, the broker minting per-user GitHub/Workspace/GCP credentials scoped ≤ the user, with **required-integration preflight** and **no default fallback**. The whole thing sits on **per-user SSO auth** and **enforced per-team namespaces** (authz + broker + per-request RLS), with the **kernel verified live** and read-only/scope **encoded as IAM/policy, not prompt**.

### 11.6 Residual risks & decisions

- **Broker is a high-value component** — it can mint many identities. Mitigate: dedicated broker SA (impersonation perms isolated from the runtime SA and from `shell__exec`), strict policy gate, full audit, rate limits, possibly a separate least-privilege process.
- **Self-impersonation hole** — if agents keep `shell__exec gcloud --impersonate-service-account`, they bypass the broker. Decision: restrict the auditor's gcloud to broker-injected short-lived tokens (no impersonation flag) or policy-deny the flag.
- **Per-user SA sprawl** — N users × SAs + grants. Mitigate: Terraform automation, lifecycle/cleanup, or prefer per-user OAuth where scope=user is acceptable.
- **Workspace/GCP token theft** — short TTLs, no `os.environ`, no read-back, revocation paths.
- **SSO bootstrap** — until SSO lands, OBO can't be fully enforced; sequence auth first.
- **Open decisions for you:** (a) GCP-OBO = per-user SA impersonation vs per-user OAuth; (b) external auditor host yes/no; (c) hard-exclude via deny-policy/VPC-SC vs simply no-grant.

### 11.7 Compute placement — where the agent code physically runs

Identity (§11.0) is *who* the agent is; **compute placement** is *where the agent process lives*. They're orthogonal axes that compose. The original Step 2 covered identity well and underspecified placement — this section closes that gap. Diagram: `agent-mcp-security-compute-topology.drawio`.

| Option | Where the agent runs | Identity | Verdict |
|---|---|---|---|
| **T1** Today's monolith | One Cloud Run container, all agents as async tasks | One shared runtime SA (~Editor) | Status quo · HIGH risk · migrate away |
| **T2** Autoscale pods (central, per-agent) | Per-agent pod (GKE Autopilot or Cloud Run Jobs); per-invocation for OBO | Per-pod SA via Workload Identity | ✅ for **interactive / OBO** agents |
| **T3** Full agent in each project | A complete LLM agent (tools + shell) inside every audited project | The project's in-project SA | Valid for tiny estates; expensive at scale; **puts the LLM surface inside the audited projects** |
| **T4** Hybrid: central brain + per-project collector | Central LLM/broker OUTSIDE the projects; tiny **collector** inside each project | Collector uses in-project SA via WIF | ✅ **recommended for multi-project audit** |

#### Why T4 over T3 (the user's "agent per project + A2A")

T3 deploys a full LLM-driven agent into each audited project — which puts the *most dangerous* component (LLM + prompt-injection + arbitrary shell via `shell__exec`) *inside the very environment we're trying to protect*. T4 splits the responsibilities:

- The **central forgeos LLM** stays outside the audited projects (it has no GCP read role itself). It plans the audit, reads results, composes reports.
- A **deterministic collector** (no LLM, no shell, no tool-use — a small static service) is deployed *inside* each audited project, with that project's in-project SA via **Workload Identity Federation**. It accepts well-defined jobs and runs an **allowlisted** set of `gcloud … list/describe` commands. Code is small enough to formally review.
- Central and collectors communicate via **Pub/Sub** (authenticated; OIDC-signed messages). Central publishes `audit-jobs`; collectors subscribe filtered by their `project_id`; results return on `audit-results`. **Central never reaches into a project; collectors only reach OUT.**
- A project without a deployed collector is **unreachable by design** — natural hard-exclude.

Net effect: the LLM/prompt-injection blast radius stays *outside* the audited estate; each project has native least-privilege identity; per-project footprint is a tiny auditable binary; no central master SA holds read across the estate.

#### Is autoscale (T2) secure?

Yes — and more secure than today (T1). Each invocation gets a fresh pod with **its own workload identity** (no shared SA between agents); the pod dies after the run, so there's no shared memory across users' invocations or across agents' runs. **For interactive / OBO agents this is the recommended runtime** — it composes naturally with the broker (broker mints per-user creds into a fresh per-user pod). It does *not* solve the multi-project audit problem on its own, because the pods still live in the *central* GCP project; cross-project access still needs the impersonation/collector story.

#### Composing with the broker

- **T1 / T2 / T3** all rely on the broker (or some other mechanism) to mint cross-project read tokens centrally — the broker becomes the high-value SPOF (§13.3 — separate-service broker requirement).
- **T4** *minimizes* central impersonation needs: collectors use their own in-project SAs natively; the broker is only needed for the central agent's own service identity and for OBO/interactive agents. Cross-project read power is decentralized across N narrow collector SAs — no single identity that can read everything.

#### Recommendation by use case

| Use case | Recommended compute |
|---|---|
| Multi-project audit (sre-gcp-auditor scaled out) | **T4** (central brain + per-project collectors) |
| Interactive / OBO agents (user-launched) | **T2** (autoscale pods with per-pod WI) |
| Small estate (≤ ~5 projects), low complexity tolerance | T1 hardened with broker + hardened-A2 (§11.1) for the audit identity — defer T4 |
| Maximum auditor independence (prod / security-critical) | T4 + central in a separate org/region (combine with A4 §11.1) |

#### Caveats / open decisions

- **Collector discipline.** T4 only works if the collector stays small and deterministic. If it grows tool-use / LLM logic, it collapses to T3 with all T3's costs.
- **Deployment privilege paradox.** Deploying a collector into project X requires IAM in X — i.e., whoever can deploy a collector effectively grants themselves read on X. Acceptable for trusted internal admins; name explicitly in the runbook.
- **Cold start / cadence.** Daily scheduled audit: cold start irrelevant. Continuous monitoring: pre-warm with `min-instances=1`.
- **Cost.** Collectors are tiny (Cloud Run Jobs, zero idle); N × ≈$0 baseline.
- **Workspace SAs have no personal Drive storage quota** (verified on 2026-05-28 while testing the first drive-chat-agent). A bare service-account identity cannot create files in My Drive — `403 storageQuotaExceeded`. For any agent that needs to *create* Workspace files, the working area must be a **Shared Drive** with the SA as Content Manager (or rely on Domain-Wide Delegation, which is heavier). Read/update of existing user files via SA + Drive share still works. This is a Google policy, not a configuration we can override; bake it into the runbook for every Workspace-writing agent.

## 12. Step 3 — draw.io diagrams (DONE)

Three diagrams in `docs/security/` (open in diagrams.net or the VS Code Draw.io extension):

- **`agent-mcp-security-architecture.drawio`** — the formal architecture (below, 5 pages).
- **`agent-mcp-security-analogy.drawio`** — the plain-English building/keys analogy (2 pages: Before · After). The starting point if the architecture diagram is too dense.
- **`agent-mcp-security-compute-topology.drawio`** — the **compute placement** axis (§11.7): T1/T2/T3/T4 side-by-side + a T4-detail page showing the central-brain + per-project-collector flow.

The architecture diagram has 5 pages, status-colored (🟥 vulnerable · 🟧 pending · 🟦 infra · 🟩 secure · 🟪 broker):

1. **Before (today)** — shared container + ambient ~Editor SA, PAT-to-prompt leak, single Workspace identity, unauthenticated PAT-write, A2A cred inheritance.
2. **After (target)** — SSO/JWT → two planes (OBO + service identity) → separate-service Credential Broker → per-user/per-service short-lived creds; enforced namespaces; hardened-A2 auditor; external audit sink.
3. **Example: `sre-gcp-auditor`** before/after (prompt-promise read-only → IAM-enforced allowlist via broker-impersonated audit SA).
4. **Example: interactive OBO agent** before/after (one shared Workspace identity → acts as the launching user, per-user creds, preflight, run-private).
5. **Roadmap** — Phase 0 correctness gates (pending, partly exploitable now) → Phase 1 foundation → Phase 2 broker → Phase 3 hardened-A2 auditor → Phase 4 defense-in-depth; risk HIGH→MED→LOW.

---

## 13. Security review — findings, verification & revised plan

A principal-security-architect agent red-teamed Steps 1–2. I **independently verified** its load-bearing claims against the code; the critical ones are confirmed. **This section is authoritative where it conflicts with earlier sections.**

### 13.1 Verified critical findings (exploitable / thesis-breaking)

1. **Secret-to-prompt leak (CONFIRMED end-to-end).** `executor.invoke` populates `invoke_ctx["_credentials"]` (`executor.py:351-356`) and passes that dict to the adapter (`executor.py:359`); the forgeos adapter forwards it as `context` (`stacks/forgeos/adapter.py:72`); the loop serializes it into the **LLM user prompt**: `user_content += "\n\nContext: " + json.dumps(context)` (`agentic_loop.py:140-141`). **The GitHub PAT is sent to the model** (and any prompt logging) — worse than `os.environ`. Overturns Step 1's "GitHub is the correct pattern" (corrected in §1).
2. **Injected creds don't reach tools via the documented path (CONFIRMED).** Tools get `agent_context = build_agent_context(...)` (`adapter.py:71`), which has **no `_credentials`** (`stacks/base.py:239-249`); `_gh_token_from_ctx(agent_context)` (`dev_tools.py:393-411`) gets nothing — explaining the `x-access-token` git-push fallback. The plumbing is broken *and* leaky; fix before generalizing.
3. **`/api/credentials/github` is UNAUTHENTICATED (CONFIRMED).** `put_github_credential` (`fastapi_app.py:3247-3268`) has **no `Depends(check_auth)`**; `user_id` comes from the request body, `caller` from a self-asserted header/IP. Anyone who can reach it can plant a PAT under any `user_id`, then trigger an OBO run as that victim. **Fix immediately.**

### 13.2 Findings accepted on the reviewer's evidence (confirm during build)

Reviewer cited file:line; judged sound, not personally re-verified this round: caller-supplied `user_id` trusted at `executor.py:347` (confused-deputy for OBO); **A2A copies `_credentials` to callees by default** (`a2a.py:86-103,264`) — nullifies namespace cred-isolation; the **real kernel ships** (not CE stubs) but has **no identity stage** and `check_tool_call` **fails open with no registry wired** (`_facade.py:273-275`), `FORGEOS_KERNEL_MODE=production` forces the import to raise instead of silently stubbing; Cloud Run runs as the **default compute SA (≈Editor), `service_account` unset** in `infrastructure/terraform/gcp/main.tf`, egress `PRIVATE_RANGES_ONLY` (public egress open); `dev-*` bearer accepted in `check_auth`; `bash`/`sh` on the shell allowlist ⇒ arbitrary shell under ambient ADC (prompt-injection ≈ RCE-with-cloud-creds). Also: Workspace token scope is narrow (`gmail.send` + `drive.metadata.readonly`) — §5.2 overstated "full Gmail/Drive."

### 13.3 Revised Step 2 decisions

- **Broker = SEPARATE Cloud Run service** with its own SA holding the impersonation/mint rights. Cloud Run is one-SA-per-service, so an in-process broker shares its SA with agent `shell__exec` ⇒ self-impersonation is the *default*, not a footnote. Agent-runtime SA gets **no** `serviceAccountTokenCreator`; policy-deny `--impersonate-service-account` in `shell__exec`.
- **Auditor topology: replace A3 with hardened-A2.** Per-project SA sprawl buys isolation you don't keep (broker can impersonate all ⇒ blast radius = the broker). Adopt: **one dedicated audit SA**, a **custom least-privilege read role** (NOT `roles/viewer`, which exposes IAM/secret metadata), **folder-level grants** for allowlisted folders, **IAM Deny policies + VPC Service Controls** as the *hard* exclude/exfil wall (not "absence of grant"), and an **append-only audit log sink in a separate project**. Make **A4 (external host) the default** for prod/security-sensitive audits (auditor independence).
- **GCP-OBO semantics — decide explicitly (was a footnote):** *scoped per-user service identity* (SA impersonation; you own the grants; easier; **not** true OBO) **vs** *the user's own authority* (per-user OAuth / Workforce Identity Federation; true OBO, but `cloud-platform` consent is broad and often **blocked by org policy**). Pick before building.

### 13.4 Correctness gates — MUST precede the broker (secure MVP slice, in order)

1. **Authenticate + rate-limit `/api/credentials/github` and every write route; remove the `dev-*` bypass in prod.**
2. **Derive `user_id` strictly from the verified auth principal; executor ignores body-supplied `user_id`.**
3. **Stop serializing `context`/credentials into the prompt; deliver `_credentials` to tools via `agent_context`** (and redact secrets from audit `args`/`cmd`, not only by key name).
4. **A2A: default `isolated()`; strip `_credentials` on every hop** (OBO must not transit A2A — callees re-broker if they need user creds).
5. **Explicit least-privilege Cloud Run runtime SA** (drop default-compute Editor); ingress **internal + IAP**; **VPC-SC + egress allowlist**.
6. **Wire the registry into the kernel and set `FORGEOS_KERNEL_MODE=production`** so tool allowlists enforce and the kernel can't fail open / silently stub.

These are *correctness gates*, not parallel prerequisites: #1 is exploitable today; #2–#4 nullify the broker's guarantees if skipped.

### 13.5 Missing controls to add

Prompt-injection defense (enforced tool allowlists + HITL on dangerous tools), egress/exfil controls (VPC-SC + proxy), secret rotation + CMEK on Secret Manager, audit-log integrity + SIEM/alerting (broker mints, PAT writes, cross-ns A2A, `gcloud` shell), break-glass/revocation runbook, Org Policy guardrails (`disableServiceAccountKeyCreation`, domain-restricted member, OAuth-client allowlist), supply-chain/provenance for the kernel artifact, invoke rate-limit + enforced budgets, **EU data-residency** review (Drive/Workspace data into the Cloud Run region — GDPR).

### 13.6 Residual risk & top fixes

**Risk of the architecture as originally drafted, on today's substrate: HIGH** — the broker's headline guarantees (per-principal attribution; namespace cred-isolation) are nullified by caller-controlled `user_id`, A2A cred inheritance, and creds-in-prompt, atop an unauthenticated PAT-write endpoint and arbitrary shell under an ambient ~Editor SA. **With §13.4 gates first → MEDIUM; with separate-service broker + hardened-A2 + Deny/VPC-SC + per-namespace SAs + external sink → LOW.**

**Top fixes (priority):** (1) auth the credentials endpoint; (2) bind `user_id` to the verified principal; (3) stop creds-in-prompt + route to tools; (4) A2A isolation + strip creds; (5) broker as separate service/SA; (6) decide GCP-OBO semantics; (7) hardened-A2 + Deny/VPC-SC + external sink; (8) least-priv runtime SA + internal ingress + `KERNEL_MODE=production`.
