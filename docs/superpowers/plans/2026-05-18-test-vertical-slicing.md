# Test Vertical Slicing Reorganization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the flat `tests/` directory into the vertical-slice structure defined in `product/03-tech/architecture.md` — `unit/`, `integration/`, `conformance/`, `e2e/`, and `load/` each mirroring the domain verticals.

**Architecture:** No test code changes — only `git mv` to new paths and `__init__.py` creation. `pyproject.toml` `testpaths = ["tests"]` already covers recursive discovery, so no pytest config changes are required. Each new sub-directory needs an `__init__.py` for pytest collection.

**Tech Stack:** Python 3.11, pytest 8, pytest-asyncio (asyncio_mode = "auto")

---

## Complete File Mapping

| Current path | New path |
|---|---|
| `tests/test_kernel.py` | `tests/integration/kernel/test_kernel.py` |
| `tests/test_kernel_tool_gate.py` | `tests/integration/kernel/test_kernel_tool_gate.py` |
| `tests/test_platform_syscall.py` | `tests/integration/kernel/test_platform_syscall.py` |
| `tests/test_hooks.py` | `tests/integration/kernel/test_hooks.py` |
| `tests/test_admission_registers_contracts.py` | `tests/integration/kernel/test_admission_registers_contracts.py` |
| `tests/test_platform_capabilities.py` | `tests/integration/kernel/test_platform_capabilities.py` |
| `tests/test_platform_budget_reservation.py` | `tests/integration/kernel/test_platform_budget_reservation.py` |
| `tests/test_crewai_adk_kernel_gate.py` | `tests/integration/kernel/test_crewai_adk_kernel_gate.py` |
| `tests/test_platform_executor.py` | `tests/integration/agent_execution/test_platform_executor.py` |
| `tests/test_platform_registry.py` | `tests/integration/agent_execution/test_platform_registry.py` |
| `tests/test_platform_process.py` | `tests/integration/agent_execution/test_platform_process.py` |
| `tests/test_platform_checkpoint.py` | `tests/integration/agent_execution/test_platform_checkpoint.py` |
| `tests/test_platform_adapters.py` | `tests/integration/agent_execution/test_platform_adapters.py` |
| `tests/test_platform_base.py` | `tests/integration/agent_execution/test_platform_base.py` |
| `tests/test_platform_generic.py` | `tests/integration/agent_execution/test_platform_generic.py` |
| `tests/test_adk_integration.py` | `tests/integration/agent_execution/test_adk_integration.py` |
| `tests/test_crewai_tool_binding.py` | `tests/integration/agent_execution/test_crewai_tool_binding.py` |
| `tests/test_openclaw_tool_proxy.py` | `tests/integration/agent_execution/test_openclaw_tool_proxy.py` |
| `tests/test_agent_wizard_planner.py` | `tests/integration/agent_execution/test_agent_wizard_planner.py` |
| `tests/test_platform_package_registry.py` | `tests/integration/agent_execution/test_platform_package_registry.py` |
| `tests/test_sdk_runtime.py` | `tests/integration/agent_execution/test_sdk_runtime.py` |
| `tests/test_manifest_canonical.py` | `tests/unit/agent_execution/test_manifest_canonical.py` |
| `tests/test_llm_router_failover.py` | `tests/integration/workflow_execution/test_llm_router_failover.py` |
| `tests/test_llm_router_streaming.py` | `tests/integration/workflow_execution/test_llm_router_streaming.py` |
| `tests/test_cost_tracking.py` | `tests/integration/workflow_execution/test_cost_tracking.py` |
| `tests/test_model_client.py` | `tests/unit/workflow_execution/test_model_client.py` |
| `tests/test_mcp_tools.py` | `tests/integration/tool_execution/test_mcp_tools.py` |
| `tests/test_mcp_manager.py` | `tests/integration/tool_execution/test_mcp_manager.py` |
| `tests/test_tool_executor_syscall_adoption.py` | `tests/integration/tool_execution/test_tool_executor_syscall_adoption.py` |
| `tests/test_tool_retries.py` | `tests/integration/tool_execution/test_tool_retries.py` |
| `tests/test_platform_providers.py` | `tests/integration/tool_execution/test_platform_providers.py` |
| `tests/test_platform_scheduler.py` | `tests/integration/scheduling/test_platform_scheduler.py` |
| `tests/test_platform_event_bus.py` | `tests/integration/scheduling/test_platform_event_bus.py` |
| `tests/test_platform_durable_event_store.py` | `tests/integration/scheduling/test_platform_durable_event_store.py` |
| `tests/test_scheduler_apscheduler.py` | `tests/integration/scheduling/test_scheduler_apscheduler.py` |
| `tests/test_triggers_and_preemption.py` | `tests/integration/scheduling/test_triggers_and_preemption.py` |
| `tests/test_cloud_services.py` | `tests/integration/scheduling/test_cloud_services.py` |
| `tests/test_hitl_system.py` | `tests/integration/agent_communication/test_hitl_system.py` |
| `tests/test_a2a.py` | `tests/conformance/test_a2a.py` |
| `tests/test_a2a_capability_and_contract.py` | `tests/conformance/test_a2a_capability_and_contract.py` |
| `tests/test_platform_a2a_contracts.py` | `tests/conformance/test_platform_a2a_contracts.py` |
| `tests/test_a2h_protocol.py` | `tests/conformance/test_a2h_protocol.py` |
| `tests/test_h2a_protocol.py` | `tests/conformance/test_h2a_protocol.py` |
| `tests/test_intelligence_agents.py` | `tests/integration/intelligence/test_intelligence_agents.py` |
| `tests/test_connectors.py` | `tests/integration/intelligence/test_connectors.py` |
| `tests/test_ontology.py` | `tests/unit/intelligence/test_ontology.py` |
| `tests/test_rls.py` | `tests/integration/multi_tenancy/test_rls.py` |
| `tests/test_session_and_redis.py` | `tests/integration/multi_tenancy/test_session_and_redis.py` |
| `tests/test_client_store.py` | `tests/integration/multi_tenancy/test_client_store.py` |
| `tests/test_migrations.py` | `tests/integration/multi_tenancy/test_migrations.py` |
| `tests/test_admin_tools.py` | `tests/integration/multi_tenancy/test_admin_tools.py` |
| `tests/test_secrets_audit_and_leases.py` | `tests/integration/multi_tenancy/test_secrets_audit_and_leases.py` |
| `tests/test_saas_platform.py` | `tests/integration/multi_tenancy/test_saas_platform.py` |
| `tests/test_audit_log.py` | `tests/integration/observability/test_audit_log.py` |
| `tests/test_audit_hash_chain.py` | `tests/integration/observability/test_audit_hash_chain.py` |
| `tests/test_metrics.py` | `tests/integration/observability/test_metrics.py` |
| `tests/test_alerts.py` | `tests/integration/observability/test_alerts.py` |
| `tests/test_dealforge.py` | `tests/integration/verticals/test_dealforge.py` |
| `tests/test_homeforge.py` | `tests/integration/verticals/test_homeforge.py` |
| `tests/test_insureforge.py` | `tests/integration/verticals/test_insureforge.py` |
| `tests/test_travelforge.py` | `tests/integration/verticals/test_travelforge.py` |
| `tests/test_practical.py` | `tests/integration/verticals/test_practical.py` |
| `tests/test_examples.py` | `tests/e2e/test_examples.py` |
| `tests/test_all_examples.py` | `tests/e2e/test_all_examples.py` |
| `tests/test_chaos_resilience.py` | `tests/e2e/test_chaos_resilience.py` |
| `tests/load/` | `tests/load/` *(unchanged)* |

---

## Task 1: Create Directory Skeleton

**Files:**
- Create: all `__init__.py` files listed below

- [ ] **Step 1: Create all new directories with `__init__.py`**

```bash
cd /path/to/forgeos-gh

dirs=(
  tests/unit/agent_execution
  tests/unit/workflow_execution
  tests/unit/intelligence
  tests/integration/kernel
  tests/integration/agent_execution
  tests/integration/workflow_execution
  tests/integration/tool_execution
  tests/integration/scheduling
  tests/integration/agent_communication
  tests/integration/intelligence
  tests/integration/multi_tenancy
  tests/integration/observability
  tests/integration/verticals
  tests/conformance
  tests/e2e
)

for d in "${dirs[@]}"; do
  mkdir -p "$d"
  touch "$d/__init__.py"
done

# Also touch parent __init__.py files
touch tests/unit/__init__.py
touch tests/integration/__init__.py
```

- [ ] **Step 2: Verify structure**

```bash
find tests -name "__init__.py" | sort
```

Expected: one `__init__.py` per new directory (17 new files).

- [ ] **Step 3: Commit skeleton**

```bash
git add tests/unit tests/integration tests/conformance tests/e2e
git commit -m "chore: scaffold vertical-slice test directory structure"
```

---

## Task 2: Move Kernel Tests

**Files:**
- Move 8 files → `tests/integration/kernel/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_kernel.py                       tests/integration/kernel/
git mv tests/test_kernel_tool_gate.py             tests/integration/kernel/
git mv tests/test_platform_syscall.py             tests/integration/kernel/
git mv tests/test_hooks.py                        tests/integration/kernel/
git mv tests/test_admission_registers_contracts.py tests/integration/kernel/
git mv tests/test_platform_capabilities.py        tests/integration/kernel/
git mv tests/test_platform_budget_reservation.py  tests/integration/kernel/
git mv tests/test_crewai_adk_kernel_gate.py       tests/integration/kernel/
```

- [ ] **Step 2: Verify tests still pass**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/kernel/ -v --tb=short
```

Expected: same pass/fail counts as before the move (no new failures caused by moving).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/kernel/ tests/
git commit -m "chore: move kernel tests to integration/kernel/"
```

---

## Task 3: Move Agent Execution Tests

**Files:**
- Move 11 files → `tests/integration/agent_execution/`
- Move 1 file → `tests/unit/agent_execution/`

- [ ] **Step 1: Move integration files**

```bash
git mv tests/test_platform_executor.py        tests/integration/agent_execution/
git mv tests/test_platform_registry.py        tests/integration/agent_execution/
git mv tests/test_platform_process.py         tests/integration/agent_execution/
git mv tests/test_platform_checkpoint.py      tests/integration/agent_execution/
git mv tests/test_platform_adapters.py        tests/integration/agent_execution/
git mv tests/test_platform_base.py            tests/integration/agent_execution/
git mv tests/test_platform_generic.py         tests/integration/agent_execution/
git mv tests/test_adk_integration.py          tests/integration/agent_execution/
git mv tests/test_crewai_tool_binding.py      tests/integration/agent_execution/
git mv tests/test_openclaw_tool_proxy.py      tests/integration/agent_execution/
git mv tests/test_agent_wizard_planner.py     tests/integration/agent_execution/
git mv tests/test_platform_package_registry.py tests/integration/agent_execution/
git mv tests/test_sdk_runtime.py              tests/integration/agent_execution/
```

- [ ] **Step 2: Move unit file**

```bash
git mv tests/test_manifest_canonical.py tests/unit/agent_execution/
```

- [ ] **Step 3: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/agent_execution/ tests/unit/agent_execution/ -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/agent_execution/ tests/unit/agent_execution/ tests/
git commit -m "chore: move agent_execution tests to integration/ and unit/"
```

---

## Task 4: Move Workflow Execution Tests

**Files:**
- Move 3 files → `tests/integration/workflow_execution/`
- Move 1 file → `tests/unit/workflow_execution/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_llm_router_failover.py  tests/integration/workflow_execution/
git mv tests/test_llm_router_streaming.py tests/integration/workflow_execution/
git mv tests/test_cost_tracking.py        tests/integration/workflow_execution/
git mv tests/test_model_client.py         tests/unit/workflow_execution/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/workflow_execution/ tests/unit/workflow_execution/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/workflow_execution/ tests/unit/workflow_execution/ tests/
git commit -m "chore: move workflow_execution tests to integration/ and unit/"
```

---

## Task 5: Move Tool Execution Tests

**Files:**
- Move 5 files → `tests/integration/tool_execution/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_mcp_tools.py                      tests/integration/tool_execution/
git mv tests/test_mcp_manager.py                    tests/integration/tool_execution/
git mv tests/test_tool_executor_syscall_adoption.py tests/integration/tool_execution/
git mv tests/test_tool_retries.py                   tests/integration/tool_execution/
git mv tests/test_platform_providers.py             tests/integration/tool_execution/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/tool_execution/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/tool_execution/ tests/
git commit -m "chore: move tool_execution tests to integration/tool_execution/"
```

---

## Task 6: Move Scheduling Tests

**Files:**
- Move 6 files → `tests/integration/scheduling/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_platform_scheduler.py        tests/integration/scheduling/
git mv tests/test_platform_event_bus.py        tests/integration/scheduling/
git mv tests/test_platform_durable_event_store.py tests/integration/scheduling/
git mv tests/test_scheduler_apscheduler.py     tests/integration/scheduling/
git mv tests/test_triggers_and_preemption.py   tests/integration/scheduling/
git mv tests/test_cloud_services.py            tests/integration/scheduling/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/scheduling/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/scheduling/ tests/
git commit -m "chore: move scheduling tests to integration/scheduling/"
```

---

## Task 7: Move Agent Communication & Conformance Tests

**Files:**
- Move 1 file → `tests/integration/agent_communication/`
- Move 5 files → `tests/conformance/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_hitl_system.py              tests/integration/agent_communication/
git mv tests/test_a2a.py                      tests/conformance/
git mv tests/test_a2a_capability_and_contract.py tests/conformance/
git mv tests/test_platform_a2a_contracts.py   tests/conformance/
git mv tests/test_a2h_protocol.py             tests/conformance/
git mv tests/test_h2a_protocol.py             tests/conformance/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/agent_communication/ tests/conformance/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/agent_communication/ tests/conformance/ tests/
git commit -m "chore: move agent_communication and conformance tests"
```

---

## Task 8: Move Intelligence Tests

**Files:**
- Move 2 files → `tests/integration/intelligence/`
- Move 1 file → `tests/unit/intelligence/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_intelligence_agents.py tests/integration/intelligence/
git mv tests/test_connectors.py          tests/integration/intelligence/
git mv tests/test_ontology.py            tests/unit/intelligence/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/intelligence/ tests/unit/intelligence/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/intelligence/ tests/unit/intelligence/ tests/
git commit -m "chore: move intelligence tests to integration/ and unit/"
```

---

## Task 9: Move Multi-Tenancy Tests

**Files:**
- Move 7 files → `tests/integration/multi_tenancy/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_rls.py                   tests/integration/multi_tenancy/
git mv tests/test_session_and_redis.py     tests/integration/multi_tenancy/
git mv tests/test_client_store.py          tests/integration/multi_tenancy/
git mv tests/test_migrations.py            tests/integration/multi_tenancy/
git mv tests/test_admin_tools.py           tests/integration/multi_tenancy/
git mv tests/test_secrets_audit_and_leases.py tests/integration/multi_tenancy/
git mv tests/test_saas_platform.py         tests/integration/multi_tenancy/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/multi_tenancy/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/multi_tenancy/ tests/
git commit -m "chore: move multi_tenancy tests to integration/multi_tenancy/"
```

---

## Task 10: Move Observability Tests

**Files:**
- Move 4 files → `tests/integration/observability/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_audit_log.py        tests/integration/observability/
git mv tests/test_audit_hash_chain.py tests/integration/observability/
git mv tests/test_metrics.py          tests/integration/observability/
git mv tests/test_alerts.py           tests/integration/observability/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/observability/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/observability/ tests/
git commit -m "chore: move observability tests to integration/observability/"
```

---

## Task 11: Move Vertical Tests

**Files:**
- Move 5 files → `tests/integration/verticals/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_dealforge.py  tests/integration/verticals/
git mv tests/test_homeforge.py  tests/integration/verticals/
git mv tests/test_insureforge.py tests/integration/verticals/
git mv tests/test_travelforge.py tests/integration/verticals/
git mv tests/test_practical.py  tests/integration/verticals/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/integration/verticals/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/verticals/ tests/
git commit -m "chore: move verticals tests to integration/verticals/"
```

---

## Task 12: Move E2E Tests

**Files:**
- Move 3 files → `tests/e2e/`

- [ ] **Step 1: Move files**

```bash
git mv tests/test_examples.py       tests/e2e/
git mv tests/test_all_examples.py   tests/e2e/
git mv tests/test_chaos_resilience.py tests/e2e/
```

- [ ] **Step 2: Verify**

```bash
PYTHONPATH=. python3 -m pytest tests/e2e/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/ tests/
git commit -m "chore: move e2e tests to tests/e2e/"
```

---

## Task 13: Final Cleanup & Full Verification

- [ ] **Step 1: Confirm no test files remain in `tests/` root**

```bash
ls tests/test_*.py 2>/dev/null && echo "STALE FILES REMAIN" || echo "Root is clean"
```

Expected output: `Root is clean`

- [ ] **Step 2: Run full test suite**

```bash
PYTHONPATH=. python3 -m pytest tests/ --tb=short -q
```

Expected: same total pass/fail count as before the reorganization. No collection errors.

- [ ] **Step 3: Verify test counts per category**

```bash
echo "=== unit ===" && PYTHONPATH=. python3 -m pytest tests/unit/ --collect-only -q 2>&1 | tail -1
echo "=== integration ===" && PYTHONPATH=. python3 -m pytest tests/integration/ --collect-only -q 2>&1 | tail -1
echo "=== conformance ===" && PYTHONPATH=. python3 -m pytest tests/conformance/ --collect-only -q 2>&1 | tail -1
echo "=== e2e ===" && PYTHONPATH=. python3 -m pytest tests/e2e/ --collect-only -q 2>&1 | tail -1
```

- [ ] **Step 4: Update `CLAUDE.md` test commands section**

In `CLAUDE.md`, add these run commands after the existing `PYTHONPATH=. python3 -m pytest` block:

```bash
# Run by category
PYTHONPATH=. python3 -m pytest tests/unit/          # pure domain tests
PYTHONPATH=. python3 -m pytest tests/integration/   # real behaviour tests
PYTHONPATH=. python3 -m pytest tests/conformance/   # A2A / A2H / H2A protocol contracts
PYTHONPATH=. python3 -m pytest tests/e2e/           # full API flows
```

- [ ] **Step 5: Final commit**

```bash
git add CLAUDE.md
git commit -m "chore: update CLAUDE.md test commands for new vertical-slice layout"
```

---

## Self-Review

**Spec coverage:**
- ✅ `unit/` with domain verticals (`agent_execution`, `workflow_execution`, `intelligence`)
- ✅ `integration/` with all 9 verticals (`kernel`, `agent_execution`, `workflow_execution`, `tool_execution`, `scheduling`, `agent_communication`, `intelligence`, `multi_tenancy`, `observability`, `verticals`)
- ✅ `conformance/` for A2A + A2H + H2A protocol contracts
- ✅ `e2e/` for example and chaos tests
- ✅ `load/` left untouched (k6 JS files, no pytest)
- ✅ No placeholders — every step has exact commands
- ✅ All 65 test files accounted for (63 `test_*.py` + `__init__.py` + `load/`)
