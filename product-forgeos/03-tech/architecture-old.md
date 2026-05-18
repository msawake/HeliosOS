# ForgeOS Platform Architecture

ForgeOS is a multi-stack agentic platform designed to orchestrate, deploy, and manage AI agents across various frameworks. It provides a unified execution environment with support for multiple agent stacks, execution types, and ownership models.

## System Overview

The platform is split into a **Python-based Backend** and a **Next.js-based Frontend**. It follows a layered architecture with emerging patterns of **Vertical Slicing** for business domains and multi-tenant isolation.

### Core Components

#### 1. Backend (Python/FastAPI)
Located in the `src/` directory, the backend handles the heavy lifting of agent orchestration and tool execution.

*   **Core Layer (`src/core/`)**: 
	* Foundational services including:
		* Database clients,
		* LLM integrations (Claude), 
		* Hook systems for event interception, 
		* and Session management.
*   **Platform Layer (`src/platform/`)**: The "brain" of the platform.
    *   **Agent Registry**: Centralized source of truth for all registered agents (`AgentDefinition`).
    *   **Platform Executor**: Manages the lifecycle (deploy, run, stop) of agents across different stacks.
    *   **Agentic Loop (`run_agentic_loop`)**: A shared, iterative loop used by all agents to handle multi-turn history, tool execution, and cost/token enforcement.
    *   **Scheduler Engine**: Handles time-based execution for agents.
    *   **Event Bus**: Manages event-driven triggers and agent communication.
    *   **LLM Router**: Dynamically routes requests to various LLM providers (Anthropic, OpenAI).
*   **Stack Layer (`stacks/`)**: 
	* Adapters (`AgentStackAdapter`) that **normalize different agent frameworks** into a common platform interface. 
	* Supported stacks: `forgeos`, `crewai`, `adk`, and `openclaw`.
*   **MCP Layer (`src/mcp/`)**: Implements the Model Context Protocol for tool discovery and execution.
    *   **Tool Executor**: 
	    * Dispatches tool calls to either **MCP tools** (prefixed `mcp__<server>__`) or **Custom Company tools** (prefixed `company__`).
*   **Workflow Layer (`src/workflows/`)**: 
	* Orchestrates complex multi-agent sequences using a task-based dependency graph (`WorkflowTask`).
*   **Company Layer (`src/companies/`)**: 
	* Contains company-specific workflow templates and configurations (e.g., `leadforge`).

#### 2. Frontend (React/Next.js)
Located in the `dashboard/` directory.

*   **Features**: Organized by domain-specific slices (e.g., `agents/`, `workflows/`, `approvals/`, `intelligence/`).

---

## Key Mechanisms

### 1. Tool Execution Routing
The `ToolExecutor` acts as a central dispatcher. Agents provide tool calls which are routed based on prefix:
- `mcp__`: Routed to external MCP servers (e.g., Google Workspace, Slack).
- `company__`: Routed to internal platform capabilities (Event Bus, HITL, Knowledge Base).

### 2. Multi-Stack Normalization
The `AgentStackAdapter` interface allows disparate frameworks like CrewAI and Google ADK to be managed by a single `PlatformExecutor`. Each adapter translates platform-level instructions into stack-specific commands.

### 3. Multi-Tenancy & Isolation
The platform uses `tenant_id` and `company_id` across the entire stack:
- **Database**: Row-level security (RLS) and tenant filtering.
- **Execution**: `usage_enforcer` tracks token usage and costs per tenant.
- **Context**: Every agent execution is scoped to a specific tenant context.

---

## Architectural Pattern: Hybrid Analysis

The project currently uses a **Hybrid Architecture**:

*   **Layered Core**: The foundational platform components are strictly layered to ensure stability and shared infrastructure.
*   **Vertical Slicing**: Business logic (Workflows, UI Features, Company templates) is organized into domain-specific slices.

### Current Limitations for Agentic Coding Tools
*   **Monolithic Agent Definitions**: `src/platform/agent_definitions.py` is a bottleneck for LLMs.
*   **Frontend Ambiguity**: Existence of both `dashboard/` and `src/dashboard/frontend` increases search space for agents.

### Refactoring Recommendations

To optimize for **Agentic Coding Tools** (like Claude Code):

1.  **Slice Agent Definitions**: Move agent definitions into their respective domain folders (e.g., `src/intelligence/agents.py`, `src/billing/agents.py`). This allows an agent to modify a feature without loading a 1700-line registry.
2.  **Plugin-based Tooling**: Decentralize `ToolExecutor` registration so domain slices can "plug in" their own tools without touching the central dispatcher.
3.  **Consolidate Frontends**: Remove the legacy Vite frontend and standardize on the Next.js app in `dashboard/`.
4.  **Explicit Dependency Graphs**: Use more declarative workflow and task definitions to help LLMs understand the relationship between agents without tracing complex procedural code.
