---
name: ForgeOS Production Hardening & Enhancement Roadmap
description: Comprehensive implementation plan addressing critical bugs, performance bottlenecks, architecture refactoring, and developer experience improvements
goal: Transform ForgeOS from 8.2/10 to 9.5+/10 production-ready platform
version: 1.0.0
created: 2026-04-25
priority: high
estimated_duration: 12-16 weeks
phases: 4
---

# **FORGEOS IMPROVEMENT PLAN**

## **Executive Summary**

ForgeOS is a **production-grade, multi-stack AI agent platform** with strong fundamentals (Agentic Loop: 9.1/10, Parallel Orchestration: 9.5/10). However, deep exploration of the codebase revealed several critical security vulnerabilities, performance bottlenecks, and architectural abstraction leaks that must be addressed before scaling.

This plan organizes 18 high-impact improvements across 4 phases, prioritized by risk and impact.

---

## **PHASE 1: CRITICAL SECURITY & STABILITY (Weeks 1-2)**
*Priority: 🔴 CRITICAL - Must complete before production deployment*

### **1.1 Fix Credential Exposure in MCP Server Manager**
**Status:** ✅ Completed
**Location:** `src/mcp/server_manager.py`, `src/mcp/client_mcp_manager.py`
**Problem:** Environment variables containing API keys are passed directly to subprocesses and stored in plaintext in memory.
**Fix:** 
- Integrate Google Cloud Secret Manager for credential storage
- Inject credentials at runtime only (never store in `config.env_vars`)
- Implement credential rotation and memory-safe handling

### **1.2 Implement Input Validation for Tool Execution**
**Status:** ✅ Completed
**Location:** `src/mcp/tool_executor.py`
**Problem:** Tool inputs are accepted without validation against their declared `inputSchema`, risking injection attacks.
**Fix:**
- Add strict JSON schema validation using the `jsonschema` library before execution
- Implement input sanitization for shell/database tools

### **1.3 Fix Budget Manager Race Conditions**
**Status:** ✅ Completed
**Location:** `src/platform/kernel.py`
**Problem:** Non-atomic two-phase budget reservation allows concurrent tool calls to bypass daily limits.
**Fix:**
- Implement atomic check-and-reserve using `threading.RLock()` or database-backed distributed locks.

### **1.4 Fix Rate Limiter Session Isolation**
**Status:** ✅ Completed
**Location:** `src/core/hooks.py`, `src/core/redis_rate_limiter.py`
**Problem:** Multiple concurrent sessions for the same agent share rate limit counters.
**Fix:**
- Separate keys: use `session_id` for per-session limits, and `agent_id` for daily agent limits.

---

## **PHASE 2: PERFORMANCE & SCALABILITY (Weeks 3-5)**
*Priority: 🟠 HIGH - Required for high-throughput production*

### **2.1 Implement Parallel Tool Execution**
**Status:** ✅ Completed
**Location:** `src/platform/agentic_loop.py`
**Problem:** Independent tools execute sequentially within a single agent loop iteration, multiplying latency.
**Fix:**
- Use `asyncio.gather()` to execute independent tool calls in parallel.
- Add `depends_on` metadata to tools to build execution DAGs.

### **2.2 Optimize Kernel Policy Caching**
**Status:** ✅ Completed
**Location:** `src/platform/kernel.py`
**Problem:** Every tool call re-evaluates all policies from scratch (O(n) overhead).
**Fix:**
- Implement LRU caching (`@lru_cache`) with a context hash for policy evaluations.
- Batch permission checks to prevent synchronous kernel checks from blocking the async loop.

### **2.3 Fix Session Store Write Amplification**
**Status:** ✅ Completed
**Location:** `src/core/session_store.py`
**Problem:** Every new message rewrites the entire JSON array of conversation history.
**Fix:**
- Migrate to a normalized `session_messages` table (1 row per message).
- Implement paginated retrieval for long-running autonomous agents.

### **2.4 MCP Connection Pooling & Parallel Boot**
**Status:** ✅ Completed
**Location:** `src/mcp/server_manager.py`, `src/mcp/tool_executor.py`
**Problem:** Sequential server connections slow down boot; no connection pooling causes high latency per tool call.
**Fix:**
- Connect MCP servers in parallel during boot.
- Implement connection pooling (min=1, max=5) per MCP server.

---

## **PHASE 3: ARCHITECTURE REFACTORING (Weeks 6-8)**
*Priority: 🟡 MEDIUM - Improves maintainability and multi-stack support*

### **3.1 Unify the Adapter Pattern**
**Status:** ✅ Completed
**Location:** `stacks/crewai/adapter.py`, `stacks/adk/adapter.py`, `stacks/openclaw/adapter.py`
**Problem:** Framework-specific concepts leak into the base adapter interface. Tool wrapping and kernel gate checks are duplicated and inconsistent (silent failures).
**Fix:**
- Standardize tool execution semantics across all adapters.
- Enforce strict Kernel permission checks at the adapter base class level.
- Centralize model routing instead of letting each adapter handle it differently.

### **3.2 Complete Migration to Syscall Pipeline**
**Status:** ✅ Completed
**Location:** `src/core/hooks.py` (DEPRECATED), `src/platform/syscall.py`
**Problem:** Legacy hooks system is deprecated but still wired into the bootstrap and rate limiters.
**Fix:**
- Migrate `claude_client.py` and `redis_rate_limiter.py` to use `KernelDecision`.
- Delete `hooks.py` and fully transition to the 7-stage Syscall pipeline.
- Make the pipeline dynamic (skip unnecessary stages for specific syscalls to reduce overhead).

### **3.3 Distribute the Workflow Engine**
**Location:** `src/workflows/definitions.py`
**Problem:** Workflow state is stored in-memory, meaning data loss on restart and no horizontal scaling.
**Fix:**
- Migrate the standalone executor to Temporal Workflows (or Google Cloud Tasks).
- Persist workflow state in PostgreSQL.

---

## **PHASE 4: UX & API POLISH (Weeks 9-10)**
*Priority: 🟢 LOW - Improves developer and user experience*

### **4.1 Fix Frontend Request Deduplication & Caching**
**Status:** ✅ Completed
**Location:** `dashboard/src/lib/api.ts`
**Problem:** No HTTP caching or deduplication; multiple components fetch the same data simultaneously.
**Fix:**
- Implement TanStack Query (React Query) or SWR for data fetching, caching, and deduplication.

### **4.2 Non-Blocking Chat UI**
**Status:** ✅ Completed
**Location:** `dashboard/src/app/agents/[id]/chat/page.tsx`
**Problem:** The UI completely disables input while an agent is streaming a response.
**Fix:**
- Allow users to type follow-up messages during streaming.
- Add a "Cancel/Stop Generation" button.

### **4.3 API Pagination & Query Validation**
**Status:** ✅ Completed
**Location:** `src/dashboard/fastapi_app.py`
**Problem:** Endpoints like `/api/platform/agents` return all 195+ agents at once without limits.
**Fix:**
- Add `limit` and `offset` pagination to all list endpoints.
- Use Pydantic for strict query parameter validation.

### **4.4 Add Distributed Tracing**
**Location:** Platform-wide
**Problem:** Hard to debug multi-agent workflows and identify latency sources.
**Fix:**
- Integrate OpenTelemetry.
- Export traces to Jaeger or Google Cloud Trace.