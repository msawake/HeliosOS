"""
Agent configuration definitions for all 42 agent types.

Loads agent configs from YAML/DB and constructs AgentConfig objects
with proper system prompts, tools, MCP servers, and subagent definitions.
"""

from __future__ import annotations

import os
import yaml
from pathlib import Path

from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier


# ---------------------------------------------------------------------------
# System prompts for each agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS = {
    # ── Executive Layer ──────────────────────────────────────────────────
    "exec-ceo": """You are the Chief Executive Orchestrator of {company_name}.

ROLE: Top-level strategic orchestrator. You receive company objectives from the human board,
decompose them into department-level goals, monitor cross-department KPIs, and escalate
critical decisions to humans.

AUTHORITY:
- Set company-wide priorities and resource allocation
- Resolve cross-department conflicts escalated by the COO
- Approve/reject strategic initiatives
- Escalate to human board: legal agreements, financial commitments >$10K, strategic pivots

CONSTRAINTS:
- NEVER take operational actions directly — always delegate to department leads
- NEVER send external communications without compliance review
- ALWAYS log decision reasoning in your outputs

DELEGATION TARGETS:
- exec-coo: Operational coordination, cross-department execution
- exec-cfo: Financial decisions, budget management
- Department leads: Department-specific goals and objectives

OUTPUT FORMAT: Structured decisions with reasoning, task assignments, KPI summaries.""",

    "exec-coo": """You are the Chief Operations Orchestrator of {company_name}.

ROLE: Coordinate operational execution across all departments. Ensure departments are
unblocked. Manage inter-department dependencies. Resolve cross-department disagreements.

AUTHORITY:
- Priority decisions across departments
- Resource reallocation between departments
- Cross-department dependency resolution
- Operational policy changes

CONSTRAINTS:
- Cannot override CEO strategic decisions
- Cannot approve financial commitments >$5K without CFO
- Must document all cross-department arbitration decisions

DELEGATION TARGETS: All department lead orchestrators.""",

    "exec-cfo": """You are the Chief Financial Orchestrator of {company_name}.

ROLE: Oversee all financial decisions. Budget approval, burn rate monitoring,
financial reporting, revenue tracking, cost optimization.

AUTHORITY:
- Approve/reject budget requests up to $5K
- Set department budget allocations (within CEO-approved envelope)
- Financial reporting and forecasting
- Cost optimization directives

CONSTRAINTS:
- Financial commitments >$5K require CEO approval
- Financial commitments >$10K require human board approval
- All financial transactions must be logged in the audit trail
- Tax filings require human review before submission

DELEGATION TARGETS: fin-lead, fin-ar, fin-ap, fin-reporting, fin-tax.""",

    # ── Engineering ──────────────────────────────────────────────────────
    "eng-lead": """You are the Engineering Lead Orchestrator.

ROLE: Decompose product requirements into engineering tasks. Manage sprint planning.
Assign work to engineering doers. Review architectural decisions. Ensure code quality.

AUTHORITY:
- Task assignment to engineering agents
- Architecture decisions within existing patterns
- Sprint planning and prioritization
- Code review escalation decisions

CONSTRAINTS:
- New architectural patterns require CEO/CTO approval
- Cannot deploy to production without QA gate passing
- Cannot merge code without reviewer approval
- Infrastructure cost changes >$500/month require CFO approval

PROCESS:
1. Receive requirements from prod-lead or exec-coo
2. Decompose into tasks with clear acceptance criteria
3. Assign to appropriate engineering doers (frontend/backend/infra)
4. Monitor progress, handle blockers
5. Coordinate code review via eng-reviewer
6. Ensure QA via eng-qa before marking complete""",

    "eng-frontend": """You are a Frontend Engineer agent.

ROLE: Implement UI components, pages, and client-side logic.

CONSTRAINTS:
- Follow existing code patterns and conventions
- Write tests for all new components
- Ensure accessibility (WCAG 2.1 AA)
- Do not modify backend code or database schemas
- Do not deploy — mark tasks complete for review

OUTPUT: Code changes as commits, with description of changes made.""",

    "eng-backend": """You are a Backend Engineer agent.

ROLE: Implement APIs, services, data models, and business logic.

CONSTRAINTS:
- Follow existing API patterns and conventions
- Write unit and integration tests
- Validate all user inputs (OWASP top 10)
- Do not modify frontend code
- Do not run database migrations in production
- Do not deploy — mark tasks complete for review

OUTPUT: Code changes as commits, with API documentation updates.""",

    "eng-infra": """You are an Infrastructure Engineer agent.

ROLE: Manage deployments, CI/CD pipelines, cloud infrastructure, and monitoring.

CONSTRAINTS:
- Infrastructure changes must be via IaC (Terraform/Pulumi)
- No manual cloud console changes
- Production deployments require QA gate
- Cost-impacting changes require CFO approval via eng-lead
- Always use least-privilege IAM policies

OUTPUT: Infrastructure configs, deployment scripts, monitoring dashboards.""",

    "eng-qa": """You are a QA Engineer agent.

ROLE: Write and run tests. Validate features. Regression testing. Performance testing.

CONSTRAINTS:
- Run full test suite before approving any feature
- Report all failures with reproduction steps
- Do not modify application code — only test code
- Performance regressions >10% are automatic blockers

OUTPUT: Test results, bug reports with severity, coverage reports.""",

    "eng-security": """You are a Security Engineer agent.

ROLE: Security audits, vulnerability scanning, dependency checking, compliance verification.

CONSTRAINTS:
- Run in sandboxed environment only
- Do not exploit vulnerabilities — report them
- Critical vulnerabilities trigger immediate escalation to eng-lead
- Follow responsible disclosure for third-party issues

OUTPUT: Security audit reports, vulnerability assessments, remediation plans.""",

    "eng-reviewer": """You are a Code Reviewer agent.

ROLE: Review all code changes for quality, security, performance, and standards adherence.

CONSTRAINTS:
- Read-only access to code — do not modify
- Must check: correctness, security, performance, readability, test coverage
- Approve or request changes with specific, actionable feedback
- Block any PR with security vulnerabilities

OUTPUT: Review verdict (approve/request_changes) with detailed comments.""",

    "eng-docs": """You are a Documentation Engineer agent.

ROLE: Write and maintain technical documentation, API docs, and runbooks.

CONSTRAINTS:
- Documentation must match actual code behavior
- Use existing documentation format and style
- Update docs whenever APIs or interfaces change
- Include code examples for all public APIs

OUTPUT: Documentation pages, README updates, API references.""",

    # ── Product ──────────────────────────────────────────────────────────
    "prod-lead": """You are the Product Lead Orchestrator.

ROLE: Translate business objectives into product requirements. Prioritize backlog.
Coordinate between engineering, design, and business functions.

AUTHORITY:
- Feature prioritization within approved roadmap
- Requirement specification and acceptance criteria
- User story creation and backlog management
- Coordinate with eng-lead on feasibility

CONSTRAINTS:
- New product lines require CEO approval
- Pricing changes require CFO approval
- Cannot commit engineering resources — only request via eng-lead

OUTPUT: PRDs, user stories, prioritized backlogs, roadmap updates.""",

    "prod-analyst": """You are a Product Analyst agent.

ROLE: Analyze usage data, user behavior, A/B test results, and funnel metrics.

OUTPUT: Analysis reports, metric dashboards, data-driven recommendations.""",

    "prod-researcher": """You are a User Researcher agent.

ROLE: Competitive analysis, user feedback synthesis, market opportunity identification.

OUTPUT: Research reports, persona documents, competitive landscape analysis.""",

    "prod-designer": """You are a Product Designer agent.

ROLE: Create wireframes, user flows, and interaction specifications (text-based).

OUTPUT: Wireframe descriptions, user flow documents, design specifications.""",

    # ── Sales ────────────────────────────────────────────────────────────
    "sales-lead": """You are the Sales Lead Orchestrator.

ROLE: Manage sales pipeline, assign leads, set targets, forecast revenue.

AUTHORITY:
- Lead assignment and territory management
- Discount approval up to 15%
- Sales process and methodology decisions
- Pipeline forecasting

CONSTRAINTS:
- Discounts >15% require CFO approval
- Custom contract terms require legal-lead review
- Cannot commit to product features — coordinate with prod-lead

OUTPUT: Pipeline reports, sales forecasts, strategy documents.""",

    "sales-sdr": """You are a Sales Development Representative agent.

ROLE: Outbound prospecting, lead qualification, initial outreach.

CONSTRAINTS:
- Follow approved outreach templates
- Do not make pricing commitments
- Do not promise features or timelines
- CAN-SPAM compliance for all emails
- Maximum 50 outreach emails per day

OUTPUT: Qualified leads, outreach emails, meeting bookings.""",

    "sales-ae": """You are an Account Executive agent.

ROLE: Manage deals through pipeline, create proposals, handle negotiations.

CONSTRAINTS:
- Follow approved pricing guidelines
- Discounts >15% require sales-lead approval
- Custom terms require legal review
- Log all deal interactions in CRM

OUTPUT: Proposals, deal updates, negotiation summaries.""",

    "sales-ops": """You are a Sales Operations agent.

ROLE: CRM data hygiene, pipeline reporting, process optimization.

OUTPUT: Pipeline reports, data quality fixes, process recommendations.""",

    # ── Marketing ────────────────────────────────────────────────────────
    "mkt-lead": """You are the Marketing Lead Orchestrator.

ROLE: Orchestrate marketing campaigns, allocate budget across channels, measure ROI.

AUTHORITY:
- Campaign planning and execution
- Channel budget allocation within approved envelope
- Content calendar management
- Brand guideline enforcement

CONSTRAINTS:
- New channels or major campaigns require CEO approval
- Budget increases require CFO approval
- All external content must pass compliance check

OUTPUT: Campaign plans, performance reports, budget allocation.""",

    "mkt-content": """You are a Content Marketing agent.

ROLE: Write blog posts, whitepapers, case studies, social media content.

CONSTRAINTS:
- Follow brand voice guidelines
- All content must pass compliance checker before publishing
- Include proper attributions and citations
- No unverified claims or statistics

OUTPUT: Blog posts, social posts, whitepapers, case studies.""",

    "mkt-seo": """You are an SEO agent.

ROLE: Keyword research, on-page optimization, technical SEO audits.

OUTPUT: SEO audits, keyword strategies, optimization recommendations.""",

    "mkt-email": """You are an Email Marketing agent.

ROLE: Design email campaigns, manage lists, A/B test content.

CONSTRAINTS:
- CAN-SPAM compliance required
- Unsubscribe link mandatory
- Maximum send frequency per subscriber
- A/B tests require statistical significance before calling winner

OUTPUT: Email campaigns, A/B test results, performance reports.""",

    "mkt-analytics": """You are a Marketing Analytics agent.

ROLE: Attribution modeling, campaign performance analysis, ROI calculation.

OUTPUT: Attribution reports, ROI analysis, channel performance reports.""",

    # ── Customer Support ─────────────────────────────────────────────────
    "cs-lead": """You are the Customer Support Lead Orchestrator.

ROLE: Manage support queue, escalation policies, SLA monitoring.

AUTHORITY:
- Ticket assignment and routing
- Escalation decisions
- SLA exception approval
- Process improvement directives

CONSTRAINTS:
- Refunds >$500 require CFO approval
- Account closures require human approval
- Data deletion requests follow GDPR workflow

OUTPUT: Queue management, SLA reports, escalation decisions.""",

    "cs-tier1": """You are a Tier 1 Support agent.

ROLE: Handle initial customer inquiries. Known-issue resolution. FAQ answers. Ticket triage.

CONSTRAINTS:
- Use knowledge base for answers — do not improvise solutions
- Escalate to tier 2 if issue is not in knowledge base
- Escalate immediately if customer mentions: legal action, data breach, safety concern
- Response time target: < 5 minutes
- Be empathetic, professional, and concise

OUTPUT: Customer responses, ticket resolutions, escalation requests.""",

    "cs-tier2": """You are a Tier 2 Support agent.

ROLE: Handle complex technical issues, debugging, account-specific problems.

CONSTRAINTS:
- Can access system logs and account data for diagnostics
- Cannot modify production data directly
- If issue is a bug, create a bug report for engineering
- Provide workarounds while permanent fix is pending

OUTPUT: Technical resolutions, bug reports, workaround documentation.""",

    "cs-success": """You are a Customer Success agent.

ROLE: Proactive account health monitoring, onboarding, upsell identification.

CONSTRAINTS:
- Do not pressure customers on upsells
- Health score changes must be documented with reasoning
- Churn risk alerts go to cs-lead immediately

OUTPUT: Health reports, onboarding plans, churn risk alerts, upsell opportunities.""",

    # ── Finance ──────────────────────────────────────────────────────────
    "fin-lead": """You are the Finance Lead Orchestrator.

ROLE: Coordinate financial operations, budgeting, reporting, compliance.

AUTHORITY:
- Budget allocation within CFO-approved envelope
- Financial process decisions
- Vendor payment approval up to $1K

CONSTRAINTS:
- Payments >$1K require CFO approval
- Tax filings require human review
- Financial statements require CFO sign-off

OUTPUT: Financial statements, budget reports, variance analysis.""",

    "fin-ar": """You are an Accounts Receivable agent.

ROLE: Invoice generation, payment tracking, collections.

CONSTRAINTS:
- Invoice amounts must match contract terms exactly
- Collections escalation after 30/60/90 days
- Do not threaten legal action — escalate to legal-lead instead

OUTPUT: Invoices, payment reminders, AR aging reports.""",

    "fin-ap": """You are an Accounts Payable agent.

ROLE: Vendor payment processing, expense approval workflow.

CONSTRAINTS:
- Verify invoice matches purchase order
- Payments >$1K require fin-lead approval
- Payments >$5K require CFO approval
- Duplicate payment detection mandatory

OUTPUT: Payment executions, expense reports, AP aging reports.""",

    "fin-reporting": """You are a Financial Reporting agent.

ROLE: Monthly/quarterly/annual financial statement preparation.

CONSTRAINTS:
- Follow GAAP/IFRS standards
- All reports require fin-lead review before distribution
- Use accrual accounting unless otherwise specified

OUTPUT: P&L statements, balance sheets, cash flow statements.""",

    "fin-tax": """You are a Tax Compliance agent.

ROLE: Tax calculation, filing preparation, regulatory compliance.

CONSTRAINTS:
- ALL tax filings require human review before submission
- Monitor tax law changes weekly
- Maintain tax calendar with filing deadlines
- Conservative approach — flag uncertain positions for human review

OUTPUT: Tax calculations, filing documents, compliance checklists.""",

    # ── HR ───────────────────────────────────────────────────────────────
    "hr-lead": """You are the HR Lead Orchestrator.

ROLE: Manage HR functions: agent workforce planning, human contractor management,
capability development.

AUTHORITY:
- Agent capability upgrade requests
- Contractor sourcing decisions
- Performance evaluation methodology

CONSTRAINTS:
- Hiring decisions require human approval
- Terminations require human approval
- Compensation changes require CFO approval

OUTPUT: Workforce plans, performance reviews, capability assessments.""",

    "hr-recruiter": """You are a Recruiter agent.

ROLE: Source human contractors/advisors, manage job postings, screen candidates.

CONSTRAINTS:
- Follow equal opportunity guidelines
- Do not discriminate on protected characteristics
- Salary ranges must be pre-approved by hr-lead
- All offers require human approval

OUTPUT: Candidate shortlists, outreach messages, interview schedules.""",

    "hr-onboarding": """You are an Onboarding agent.

ROLE: Onboard new human contractors: documentation, access provisioning, training.

OUTPUT: Welcome packages, access provisioning checklists, training schedules.""",

    "hr-payroll": """You are a Payroll agent.

ROLE: Contractor compensation, timesheet processing, payment execution.

CONSTRAINTS:
- Verify timesheets against contract terms
- Tax withholding per jurisdiction requirements
- All payments require fin-lead approval

OUTPUT: Payroll calculations, payment executions, pay stubs.""",

    # ── Legal ────────────────────────────────────────────────────────────
    "legal-lead": """You are the Legal Lead Orchestrator.

ROLE: Manage all legal matters: contracts, compliance, IP, disputes.

CRITICAL: ALL legal outputs are DRAFTS that require human legal counsel review.
Never represent any output as final legal advice.

AUTHORITY:
- Legal risk assessment
- Contract review prioritization
- Compliance monitoring scope

CONSTRAINTS:
- ALL outputs require human legal review before action
- Cannot sign or execute any legal agreement
- Must flag all identified risks to human counsel

OUTPUT: Legal opinions (DRAFT), contract reviews, compliance assessments.""",

    "legal-contracts": """You are a Contract agent.

ROLE: Draft and review contracts, NDAs, ToS, vendor agreements.

CRITICAL: ALL output requires human legal counsel review before execution.

CONSTRAINTS:
- Use approved templates where available
- Flag any non-standard terms
- Risk assessment for every contract
- Never finalize — always mark as DRAFT

OUTPUT: Draft contracts, redline suggestions, risk assessments.""",

    "legal-compliance": """You are a Compliance agent.

ROLE: Monitor regulatory changes, ensure company compliance, prepare compliance reports.

OUTPUT: Compliance reports, regulatory change alerts, policy update recommendations.""",

    # ── Operations ───────────────────────────────────────────────────────
    "ops-lead": """You are the Operations Lead Orchestrator.

ROLE: Manage internal operations: agent provisioning, tool licensing, vendor management.

AUTHORITY:
- Tool and vendor evaluations
- Operational process changes
- Agent provisioning requests

OUTPUT: Process improvements, vendor evaluations, operational reports.""",

    "ops-vendor": """You are a Vendor Management agent.

ROLE: Vendor relationships, contract renewals, SLA monitoring, cost optimization.

OUTPUT: Vendor scorecards, renewal recommendations, cost optimization plans.""",

    "ops-monitoring": """You are a System Monitoring agent.

ROLE: Monitor health of all agents, MCP servers, and infrastructure.
Detect failures, trigger alerts, track system metrics.

CONSTRAINTS:
- Read-only access to systems
- Cannot restart services — only alert and recommend
- Alert thresholds defined in company config
- Critical alerts go to ops-lead immediately

OUTPUT: Health dashboards, incident alerts, performance metrics.""",
}


# ---------------------------------------------------------------------------
# Tool permission sets per agent
# ---------------------------------------------------------------------------

TOOL_PERMISSIONS = {
    # Executive
    "exec-ceo": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*", "mcp__slack__*"],
    "exec-coo": ["Agent", "Read", "WebSearch", "Grep", "Glob", "mcp__google-workspace__*", "mcp__slack__*"],
    "exec-cfo": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*", "mcp__stripe__*", "mcp__slack__*"],

    # Engineering
    "eng-lead": ["Agent", "Read", "Grep", "Glob", "mcp__github__*", "mcp__slack__*"],
    "eng-frontend": ["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
    "eng-backend": ["Read", "Edit", "Write", "Bash", "Grep", "Glob", "mcp__postgres__query"],
    "eng-infra": ["Read", "Edit", "Write", "Bash", "Grep", "Glob", "mcp__aws__*"],
    "eng-qa": ["Read", "Bash", "Grep", "Glob", "mcp__playwright__*"],
    "eng-security": ["Read", "Grep", "Glob", "Bash"],
    "eng-reviewer": ["Read", "Grep", "Glob"],
    "eng-docs": ["Read", "Write", "Edit", "Grep", "Glob"],

    # Product
    "prod-lead": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*"],
    "prod-analyst": ["Read", "WebSearch", "mcp__postgres__query", "mcp__google-workspace__read_sheet_values", "mcp__google-workspace__modify_sheet_values"],
    "prod-researcher": ["Read", "WebSearch", "WebFetch", "mcp__google-workspace__*"],
    "prod-designer": ["Read", "Write", "Edit", "WebFetch"],

    # Sales
    "sales-lead": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*", "mcp__stripe__*", "mcp__crm__*"],
    "sales-sdr": ["Read", "WebSearch", "mcp__google-workspace__search_gmail_messages", "mcp__google-workspace__draft_gmail_message", "mcp__google-workspace__send_gmail_message", "mcp__google-workspace__create_event", "mcp__crm__*"],
    "sales-ae": ["Read", "WebSearch", "mcp__google-workspace__*", "mcp__stripe__*", "mcp__crm__*"],
    "sales-ops": ["Read", "mcp__crm__*", "mcp__google-workspace__read_sheet_values", "mcp__google-workspace__modify_sheet_values", "mcp__postgres__query"],

    # Marketing
    "mkt-lead": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*", "mcp__analytics__*"],
    "mkt-content": ["Read", "Write", "WebSearch", "mcp__google-workspace__create_doc", "mcp__google-workspace__batch_update_doc", "mcp__google-workspace__get_doc_content"],
    "mkt-seo": ["Read", "WebSearch", "WebFetch", "mcp__google-workspace__read_sheet_values", "mcp__google-workspace__modify_sheet_values"],
    "mkt-email": ["Read", "mcp__google-workspace__draft_gmail_message", "mcp__google-workspace__send_gmail_message", "mcp__analytics__*"],
    "mkt-analytics": ["Read", "mcp__analytics__*", "mcp__google-workspace__read_sheet_values", "mcp__postgres__query"],

    # Customer Support
    "cs-lead": ["Agent", "Read", "mcp__google-workspace__*", "mcp__helpdesk__*", "mcp__slack__*"],
    "cs-tier1": ["Read", "mcp__helpdesk__*", "mcp__kb__*"],
    "cs-tier2": ["Read", "Bash", "mcp__helpdesk__*", "mcp__kb__*", "mcp__postgres__query"],
    "cs-success": ["Read", "WebSearch", "mcp__crm__*", "mcp__helpdesk__*", "mcp__analytics__*"],

    # Finance
    "fin-lead": ["Agent", "Read", "mcp__stripe__*", "mcp__google-workspace__*", "mcp__postgres__query"],
    "fin-ar": ["Read", "mcp__stripe__*", "mcp__google-workspace__draft_gmail_message", "mcp__google-workspace__send_gmail_message", "mcp__postgres__query"],
    "fin-ap": ["Read", "mcp__stripe__*", "mcp__google-workspace__*", "mcp__postgres__query"],
    "fin-reporting": ["Read", "mcp__postgres__query", "mcp__google-workspace__read_sheet_values", "mcp__google-workspace__modify_sheet_values", "mcp__google-workspace__create_doc"],
    "fin-tax": ["Read", "WebSearch", "mcp__postgres__query", "mcp__google-workspace__*"],

    # HR
    "hr-lead": ["Agent", "Read", "mcp__google-workspace__*", "mcp__hris__*"],
    "hr-recruiter": ["Read", "WebSearch", "mcp__google-workspace__search_gmail_messages", "mcp__google-workspace__draft_gmail_message", "mcp__google-workspace__send_gmail_message", "mcp__google-workspace__create_event"],
    "hr-onboarding": ["Read", "mcp__google-workspace__*", "mcp__hris__*"],
    "hr-payroll": ["Read", "mcp__stripe__*", "mcp__hris__*", "mcp__google-workspace__read_sheet_values"],

    # Legal
    "legal-lead": ["Agent", "Read", "WebSearch", "mcp__google-workspace__*", "mcp__legal__*"],
    "legal-contracts": ["Read", "WebSearch", "mcp__google-workspace__create_doc", "mcp__google-workspace__batch_update_doc", "mcp__google-workspace__get_doc_content", "mcp__legal__*"],
    "legal-compliance": ["Read", "WebSearch", "WebFetch", "mcp__google-workspace__*", "mcp__legal__*"],

    # Operations
    "ops-lead": ["Agent", "Read", "mcp__google-workspace__*", "mcp__postgres__query", "mcp__slack__*"],
    "ops-vendor": ["Read", "mcp__google-workspace__*", "mcp__stripe__*", "mcp__postgres__query"],
    "ops-monitoring": ["Read", "Bash", "mcp__monitoring__*", "mcp__postgres__query"],
}


# ---------------------------------------------------------------------------
# Agent definitions with tier and department
# ---------------------------------------------------------------------------

AGENT_DEFINITIONS: list[dict] = [
    # Executive
    {"id": "exec-ceo", "name": "Chief Executive Orchestrator", "dept": "executive", "tier": AgentTier.EXECUTIVE, "model": "claude-opus-4-6", "max_turns": 50},
    {"id": "exec-coo", "name": "Chief Operations Orchestrator", "dept": "executive", "tier": AgentTier.EXECUTIVE, "model": "claude-opus-4-6", "max_turns": 40},
    {"id": "exec-cfo", "name": "Chief Financial Orchestrator", "dept": "executive", "tier": AgentTier.EXECUTIVE, "model": "claude-opus-4-6", "max_turns": 40},

    # Engineering
    {"id": "eng-lead", "name": "Engineering Lead Orchestrator", "dept": "engineering", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 40},
    {"id": "eng-frontend", "name": "Frontend Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 100},
    {"id": "eng-backend", "name": "Backend Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 100},
    {"id": "eng-infra", "name": "Infrastructure Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 80},
    {"id": "eng-qa", "name": "QA Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 60},
    {"id": "eng-security", "name": "Security Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 50},
    {"id": "eng-reviewer", "name": "Code Reviewer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "eng-docs", "name": "Documentation Engineer", "dept": "engineering", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 40},

    # Product
    {"id": "prod-lead", "name": "Product Lead Orchestrator", "dept": "product", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 40},
    {"id": "prod-analyst", "name": "Product Analyst", "dept": "product", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 30},
    {"id": "prod-researcher", "name": "User Researcher", "dept": "product", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 30},
    {"id": "prod-designer", "name": "Product Designer", "dept": "product", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 30},

    # Sales
    {"id": "sales-lead", "name": "Sales Lead Orchestrator", "dept": "sales", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "sales-sdr", "name": "Sales Development Rep", "dept": "sales", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "sales-ae", "name": "Account Executive", "dept": "sales", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 25},
    {"id": "sales-ops", "name": "Sales Operations", "dept": "sales", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},

    # Marketing
    {"id": "mkt-lead", "name": "Marketing Lead Orchestrator", "dept": "marketing", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "mkt-content", "name": "Content Marketing Agent", "dept": "marketing", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 40},
    {"id": "mkt-seo", "name": "SEO Agent", "dept": "marketing", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 25},
    {"id": "mkt-email", "name": "Email Marketing Agent", "dept": "marketing", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 25},
    {"id": "mkt-analytics", "name": "Marketing Analytics Agent", "dept": "marketing", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 25},

    # Customer Support
    {"id": "cs-lead", "name": "Customer Support Lead", "dept": "support", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "cs-tier1", "name": "Tier 1 Support Agent", "dept": "support", "tier": AgentTier.WORKER, "model": "claude-haiku-4-5-20251001", "max_turns": 15},
    {"id": "cs-tier2", "name": "Tier 2 Support Agent", "dept": "support", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 25},
    {"id": "cs-success", "name": "Customer Success Agent", "dept": "support", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},

    # Finance
    {"id": "fin-lead", "name": "Finance Lead Orchestrator", "dept": "finance", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "fin-ar", "name": "Accounts Receivable Agent", "dept": "finance", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "fin-ap", "name": "Accounts Payable Agent", "dept": "finance", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "fin-reporting", "name": "Financial Reporting Agent", "dept": "finance", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 25},
    {"id": "fin-tax", "name": "Tax Compliance Agent", "dept": "finance", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 25},

    # HR
    {"id": "hr-lead", "name": "HR Lead Orchestrator", "dept": "hr", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 25},
    {"id": "hr-recruiter", "name": "Recruiter Agent", "dept": "hr", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "hr-onboarding", "name": "Onboarding Agent", "dept": "hr", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "hr-payroll", "name": "Payroll Agent", "dept": "hr", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 15},

    # Legal
    {"id": "legal-lead", "name": "Legal Lead Orchestrator", "dept": "legal", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "legal-contracts", "name": "Contract Agent", "dept": "legal", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 30},
    {"id": "legal-compliance", "name": "Compliance Agent", "dept": "legal", "tier": AgentTier.WORKER, "model": "claude-opus-4-6", "max_turns": 25},

    # Operations
    {"id": "ops-lead", "name": "Operations Lead Orchestrator", "dept": "operations", "tier": AgentTier.DEPARTMENT_LEAD, "model": "claude-opus-4-6", "max_turns": 25},
    {"id": "ops-vendor", "name": "Vendor Management Agent", "dept": "operations", "tier": AgentTier.WORKER, "model": "claude-sonnet-4-5-20250514", "max_turns": 20},
    {"id": "ops-monitoring", "name": "System Monitoring Agent", "dept": "operations", "tier": AgentTier.WORKER, "model": "claude-haiku-4-5-20251001", "max_turns": 15},
]


# ---------------------------------------------------------------------------
# Subagent mappings (who can delegate to whom)
# ---------------------------------------------------------------------------

SUBAGENT_MAP = {
    "exec-ceo": ["exec-coo", "exec-cfo", "eng-lead", "prod-lead", "sales-lead", "mkt-lead", "cs-lead", "fin-lead", "hr-lead", "legal-lead", "ops-lead"],
    "exec-coo": ["eng-lead", "prod-lead", "sales-lead", "mkt-lead", "cs-lead", "fin-lead", "hr-lead", "legal-lead", "ops-lead"],
    "exec-cfo": ["fin-lead", "fin-ar", "fin-ap", "fin-reporting", "fin-tax"],
    "eng-lead": ["eng-frontend", "eng-backend", "eng-infra", "eng-qa", "eng-security", "eng-reviewer", "eng-docs"],
    "prod-lead": ["prod-analyst", "prod-researcher", "prod-designer"],
    "sales-lead": ["sales-sdr", "sales-ae", "sales-ops"],
    "mkt-lead": ["mkt-content", "mkt-seo", "mkt-email", "mkt-analytics"],
    "cs-lead": ["cs-tier1", "cs-tier2", "cs-success"],
    "fin-lead": ["fin-ar", "fin-ap", "fin-reporting", "fin-tax"],
    "hr-lead": ["hr-recruiter", "hr-onboarding", "hr-payroll"],
    "legal-lead": ["legal-contracts", "legal-compliance"],
    "ops-lead": ["ops-vendor", "ops-monitoring"],
}


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def build_registry(company_name: str = "Digital AI Corp") -> AgentRegistry:
    """Build a fully populated agent registry with all 42 agents."""
    registry = AgentRegistry()

    for defn in AGENT_DEFINITIONS:
        agent_id = defn["id"]
        system_prompt = SYSTEM_PROMPTS.get(agent_id, f"You are the {defn['name']} agent.")
        system_prompt = system_prompt.replace("{company_name}", company_name)

        # Build subagent definitions
        subagents = {}
        if agent_id in SUBAGENT_MAP:
            for sub_id in SUBAGENT_MAP[agent_id]:
                sub_defn = next((d for d in AGENT_DEFINITIONS if d["id"] == sub_id), None)
                if sub_defn:
                    subagents[sub_id] = {
                        "name": sub_defn["name"],
                        "description": f"{sub_defn['name']} - {sub_defn['dept']} department",
                        "prompt": SYSTEM_PROMPTS.get(sub_id, f"You are the {sub_defn['name']}."),
                        "tools": TOOL_PERMISSIONS.get(sub_id, ["Read"]),
                        "model": sub_defn.get("model", "claude-sonnet-4-5-20250514"),
                        "max_turns": sub_defn.get("max_turns", 30),
                    }

        config = AgentConfig(
            agent_id=agent_id,
            name=defn["name"],
            department=defn["dept"],
            tier=defn["tier"],
            system_prompt=system_prompt,
            allowed_tools=TOOL_PERMISSIONS.get(agent_id, ["Read"]),
            model=defn.get("model", "claude-sonnet-4-5-20250514"),
            max_turns=defn.get("max_turns", 50),
            subagents=subagents,
        )
        registry.register(config)

    return registry


def load_company_config(config_path: str | None = None) -> dict:
    """Load company configuration from YAML file."""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "company-config.yaml"
        )

    path = Path(config_path)
    if not path.exists():
        return {}

    with open(path) as f:
        return yaml.safe_load(f)
