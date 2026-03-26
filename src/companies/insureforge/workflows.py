"""
InsureForge AI workflow templates.

6 pre-built workflow definitions for InsureForge's insurance comparison operations.
"""

from __future__ import annotations

from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    WorkflowDefinition,
)


def create_quote_comparison_workflow(
    user_id: str,
    insurance_type: str,
    state: str,
    coverage_level: str = "recommended",
) -> WorkflowDefinition:
    """Quote comparison workflow — intake, quote, compare, recommend."""
    builder = TaskGraphBuilder(f"Quote Comparison: {insurance_type} for {user_id}", "operational")

    builder.task(
        name="intake",
        agent="intake-agent",
        description=f"Collect {insurance_type} insurance information from user {user_id} in {state}. Gather required data for accurate quoting.",
        budget_tokens=15_000,
    ).task(
        name="build_profile",
        agent="profile-builder",
        description=f"Build insurance profile for user {user_id}. Enrich with public data, calculate risk factors for {insurance_type} in {state}.",
        budget_tokens=15_000,
        blocked_by=["intake"],
    ).task(
        name="generate_quotes",
        agent=f"quote-{insurance_type}" if insurance_type in ("auto", "home", "life", "health") else "quote-auto",
        description=f"Generate {insurance_type} insurance quotes from all available carriers for user {user_id} in {state}. Coverage level: {coverage_level}.",
        budget_tokens=20_000,
        blocked_by=["build_profile"],
    ).task(
        name="compliance_check",
        agent="compliance-agent",
        description=f"Verify quotes comply with {state} insurance regulations. Check required disclosures and coverage minimums for {insurance_type}.",
        budget_tokens=10_000,
        blocked_by=["generate_quotes"],
    ).task(
        name="compare_quotes",
        agent="compare-agent",
        description=f"Compare all {insurance_type} quotes for user {user_id}. Create side-by-side comparison with coverage details, pricing, and carrier ratings.",
        budget_tokens=20_000,
        blocked_by=["compliance_check"],
    ).task(
        name="recommend",
        agent="recommend-agent",
        description=f"Generate personalized {insurance_type} insurance recommendation for user {user_id}. Consider coverage needs, budget, and carrier quality.",
        budget_tokens=20_000,
        blocked_by=["compare_quotes"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "insurance_type": insurance_type,
        "state": state,
        "coverage_level": coverage_level,
    }
    return workflow


def create_application_workflow(
    user_id: str,
    carrier: str,
    insurance_type: str,
    policy_id: str,
    premium: float,
) -> WorkflowDefinition:
    """Application and binding workflow — apply to carrier, compliance review, submit."""
    builder = TaskGraphBuilder(f"Application: {carrier} {insurance_type} for {user_id}", "operational")

    builder.task(
        name="prepare_application",
        agent="application-agent",
        description=f"Prepare {insurance_type} application for user {user_id} with {carrier}. Pre-fill from profile data. Policy: {policy_id}, premium: ${premium}/month.",
        budget_tokens=15_000,
    ).task(
        name="compliance_review",
        agent="compliance-agent",
        description=f"Review application for {carrier} {insurance_type}. Verify state compliance, required disclosures, and data accuracy.",
        budget_tokens=10_000,
        blocked_by=["prepare_application"],
    ).task(
        name="submit_application",
        agent="application-agent",
        description=f"Submit approved application to {carrier} for user {user_id}. Handle carrier API submission and obtain confirmation. Policy: {policy_id}.",
        budget_tokens=15_000,
        blocked_by=["compliance_review"],
    ).task(
        name="track_referral",
        agent="fin-billing",
        description=f"Record referral for {carrier} {insurance_type} policy {policy_id}. Track for referral fee payment. Premium: ${premium}/month.",
        budget_tokens=10_000,
        blocked_by=["submit_application"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "carrier": carrier,
        "insurance_type": insurance_type,
        "policy_id": policy_id,
        "premium": premium,
    }
    return workflow


def create_claims_support_workflow(
    user_id: str,
    policy_id: str,
    carrier: str,
    claim_type: str,
    description: str,
) -> WorkflowDefinition:
    """Claims support workflow — guide user through carrier claims process."""
    builder = TaskGraphBuilder(f"Claims Support: {claim_type} for {user_id}", "operational")

    builder.task(
        name="assess_claim",
        agent="claims-support",
        description=f"Assess claim for user {user_id}. Policy: {policy_id} with {carrier}. Claim type: {claim_type}. Details: {description}",
        budget_tokens=15_000,
    ).task(
        name="coverage_review",
        agent="compare-agent",
        description=f"Review coverage for policy {policy_id} with {carrier}. Determine if {claim_type} claim is likely covered under the policy terms.",
        budget_tokens=15_000,
        blocked_by=["assess_claim"],
    ).task(
        name="guide_filing",
        agent="claims-support",
        description=f"Guide user {user_id} through {carrier}'s claims filing process for {claim_type}. Provide required documentation list and carrier contact info.",
        budget_tokens=15_000,
        blocked_by=["coverage_review"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "policy_id": policy_id,
        "carrier": carrier,
        "claim_type": claim_type,
    }
    return workflow


def create_policy_renewal_workflow(
    user_id: str,
    policy_id: str,
    carrier: str,
    insurance_type: str,
    current_premium: float,
) -> WorkflowDefinition:
    """Policy renewal workflow — re-quote, compare, recommend renewal or switch."""
    builder = TaskGraphBuilder(f"Renewal: {insurance_type} {policy_id} for {user_id}", "operational")

    builder.task(
        name="current_policy_review",
        agent="compare-agent",
        description=f"Review current {insurance_type} policy {policy_id} with {carrier} for user {user_id}. Current premium: ${current_premium}/month. Identify coverage gaps or excess.",
        budget_tokens=15_000,
    ).task(
        name="market_requote",
        agent=f"quote-{insurance_type}" if insurance_type in ("auto", "home", "life", "health") else "quote-auto",
        description=f"Generate fresh {insurance_type} quotes from all carriers for user {user_id}. Compare against renewal premium of ${current_premium}/month with {carrier}.",
        budget_tokens=20_000,
        blocked_by=["current_policy_review"],
    ).task(
        name="renewal_recommendation",
        agent="recommend-agent",
        description=f"Recommend whether user {user_id} should renew {insurance_type} with {carrier} at ${current_premium}/month or switch. Show savings potential.",
        budget_tokens=20_000,
        blocked_by=["market_requote"],
    ).task(
        name="notify_user",
        agent="support-agent",
        description=f"Notify user {user_id} about renewal options for {insurance_type} policy {policy_id}. Present recommendation and next steps.",
        budget_tokens=10_000,
        blocked_by=["renewal_recommendation"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "policy_id": policy_id,
        "carrier": carrier,
        "insurance_type": insurance_type,
        "current_premium": current_premium,
    }
    return workflow


def create_customer_onboarding_workflow(
    user_id: str,
    user_email: str,
    insurance_interests: list[str],
    state: str,
) -> WorkflowDefinition:
    """Customer onboarding workflow — profile, initial quotes, and welcome."""
    builder = TaskGraphBuilder(f"Onboarding: {user_id}", "operational")
    interests_str = ", ".join(insurance_interests)

    builder.task(
        name="setup_profile",
        agent="intake-agent",
        description=f"Set up user profile for {user_id} ({user_email}) in {state}. Insurance interests: {interests_str}.",
        budget_tokens=15_000,
    ).task(
        name="initial_quotes",
        agent="quotes-lead",
        description=f"Generate initial quotes for new user {user_id} in {state}. Insurance types: {interests_str}. Show value immediately.",
        budget_tokens=25_000,
        blocked_by=["setup_profile"],
    ).task(
        name="welcome_email",
        agent="support-agent",
        description=f"Send welcome email to {user_email} with initial quote summary. Include tips for comparing coverage and next steps.",
        budget_tokens=10_000,
        blocked_by=["initial_quotes"],
    ).task(
        name="compliance_verify",
        agent="compliance-agent",
        description=f"Verify onboarding flow for {state} compliance. Check required disclosures and data handling for {interests_str}.",
        budget_tokens=10_000,
        blocked_by=["setup_profile"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "user_email": user_email,
        "insurance_interests": insurance_interests,
        "state": state,
    }
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    target_insurance_types: list[str],
) -> WorkflowDefinition:
    """Marketing campaign workflow for InsureForge user acquisition."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")
    types_str = ", ".join(target_insurance_types)

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}. Goal: {campaign_goal}. Budget: ${budget_usd}. Target types: {types_str}.",
        budget_tokens=40_000,
    ).task(
        name="compliance_review",
        agent="compliance-agent",
        description=f"Review campaign strategy for state insurance advertising compliance. Campaign: {campaign_name}. Types: {types_str}.",
        budget_tokens=15_000,
        blocked_by=["strategy"],
    ).task(
        name="content",
        agent="mkt-content",
        description=f"Create content for campaign: {campaign_name}. Insurance comparison guides and landing pages for: {types_str}.",
        budget_tokens=60_000,
        blocked_by=["compliance_review"],
    ).task(
        name="ad_setup",
        agent="mkt-ppc",
        description=f"Set up Google Ads for campaign: {campaign_name}. Insurance keywords for: {types_str}. Budget: ${budget_usd}. Note: high CPC category.",
        budget_tokens=40_000,
        blocked_by=["compliance_review"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels.",
        budget_tokens=15_000,
        blocked_by=["content", "ad_setup"],
    ).task(
        name="analytics",
        agent="mkt-analytics",
        description=f"Track campaign: {campaign_name}. Monitor cost-per-quote, quote-to-bind, and referral fee ROI against goal: {campaign_goal}.",
        budget_tokens=25_000,
        blocked_by=["launch"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "campaign_name": campaign_name,
        "budget_usd": budget_usd,
        "target_insurance_types": target_insurance_types,
    }
    return workflow
