"""
CRM-via-Ontology provider.

Stores and queries leads as objects in the intelligence ontology, giving
ForgeOS a real, persistent, queryable CRM without needing Salesforce or
HubSpot credentials. Each lead is an `ObjectInstance` of type `Lead`.

Activities (calls, emails, meetings) are stored as `Activity` objects
linked to a `Lead` via a `HasActivity` link.

Env flag: FORGEOS_ENABLE_REAL_CRM=1
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Module-level ontology instance — populated by the first call or by
# explicit handoff from the bootstrap layer.
_ontology: Any | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ontology(agent_context: dict | None):
    """Resolve the ontology instance from context or lazy-init."""
    global _ontology
    if agent_context and agent_context.get("_ontology") is not None:
        return agent_context["_ontology"]
    if _ontology is not None:
        return _ontology
    try:
        from src.intelligence.ontology import InMemoryOntology, ObjectType, PropertyDef
        _ontology = InMemoryOntology()
        # Register the minimal CRM schema
        lead_props = {
            "name": PropertyDef(name="name", type="string", required=True),
            "email": PropertyDef(name="email", type="string"),
            "company": PropertyDef(name="company", type="string"),
            "title": PropertyDef(name="title", type="string"),
            "stage": PropertyDef(name="stage", type="enum",
                                 enum_values=["prospect", "qualified", "negotiation", "closed_won", "closed_lost"]),
            "score": PropertyDef(name="score", type="number"),
            "source": PropertyDef(name="source", type="string"),
            "notes": PropertyDef(name="notes", type="string"),
        }
        _ontology.register_type(ObjectType(name="Lead", properties=lead_props, description="Sales lead"))

        activity_props = {
            "lead_id": PropertyDef(name="lead_id", type="string", required=True),
            "kind": PropertyDef(name="kind", type="enum",
                                enum_values=["call", "email", "meeting", "note", "task"]),
            "subject": PropertyDef(name="subject", type="string"),
            "body": PropertyDef(name="body", type="string"),
            "created_by": PropertyDef(name="created_by", type="string"),
            "created_at": PropertyDef(name="created_at", type="date"),
        }
        _ontology.register_type(ObjectType(name="Activity", properties=activity_props,
                                           description="CRM activity"))
        return _ontology
    except Exception as e:
        logger.warning("Failed to initialize CRM ontology: %s", e)
        return None


def handle_crm_search_leads(tool_input: dict, agent_context: dict | None) -> dict:
    """Search leads stored in the ontology by stage/company/score/etc."""
    onto = _get_ontology(agent_context)
    if onto is None:
        return {"success": False, "error": "ontology unavailable"}

    query = tool_input.get("query", "").strip().lower()
    stage = tool_input.get("stage")
    min_score = tool_input.get("min_score")
    limit = int(tool_input.get("limit", 20))

    filters = {}
    if stage:
        filters["stage"] = stage

    try:
        objects = onto.query_objects("Lead", filters=filters or None, limit=limit * 4)
    except Exception as e:
        return {"success": False, "error": f"query failed: {e}"}

    leads = []
    for obj in objects:
        props = obj.properties or {}
        if query:
            blob = " ".join(str(props.get(k, "")) for k in ("name", "email", "company", "notes")).lower()
            if query not in blob:
                continue
        if min_score is not None and props.get("score", 0) < min_score:
            continue
        leads.append({
            "id": obj.id,
            "name": props.get("name", ""),
            "email": props.get("email", ""),
            "company": props.get("company", ""),
            "title": props.get("title", ""),
            "stage": props.get("stage", "prospect"),
            "score": props.get("score", 0),
            "source": props.get("source", ""),
            "updated_at": obj.updated_at,
        })
        if len(leads) >= limit:
            break

    return {
        "success": True,
        "count": len(leads),
        "leads": leads,
        "backend": "ontology",
    }


def handle_crm_update_lead(tool_input: dict, agent_context: dict | None) -> dict:
    """Create or update a lead in the ontology."""
    onto = _get_ontology(agent_context)
    if onto is None:
        return {"success": False, "error": "ontology unavailable"}

    from src.intelligence.ontology import ObjectInstance

    lead_id = tool_input.get("lead_id") or tool_input.get("id") or ""
    props = {
        k: v for k, v in tool_input.items()
        if k in {"name", "email", "company", "title", "stage", "score", "source", "notes"}
        and v is not None
    }
    if not props:
        return {"success": False, "error": "no lead fields provided"}

    if lead_id:
        obj = ObjectInstance(
            id=lead_id,
            type_name="Lead",
            properties=props,
            source=(agent_context or {}).get("agent_id", "agent"),
        )
    else:
        obj = ObjectInstance(
            type_name="Lead",
            properties=props,
            source=(agent_context or {}).get("agent_id", "agent"),
        )
    try:
        new_id = onto.upsert_object(obj)
    except Exception as e:
        return {"success": False, "error": f"upsert failed: {e}"}

    return {
        "success": True,
        "lead_id": new_id,
        "action": "updated" if lead_id else "created",
        "properties": props,
        "backend": "ontology",
    }


def handle_crm_create_activity(tool_input: dict, agent_context: dict | None) -> dict:
    """Record a CRM activity (call, email, meeting, note, task) on a lead."""
    onto = _get_ontology(agent_context)
    if onto is None:
        return {"success": False, "error": "ontology unavailable"}

    from src.intelligence.ontology import ObjectInstance

    lead_id = tool_input.get("lead_id")
    kind = tool_input.get("kind", "note")
    subject = tool_input.get("subject", "")
    body = tool_input.get("body", "")

    if not lead_id:
        return {"success": False, "error": "lead_id is required"}

    # Verify lead exists
    try:
        lead = onto.get_object(lead_id)
    except Exception as e:
        return {"success": False, "error": f"lead lookup failed: {e}"}
    if not lead:
        return {"success": False, "error": f"lead {lead_id} not found"}

    activity = ObjectInstance(
        type_name="Activity",
        properties={
            "lead_id": lead_id,
            "kind": kind,
            "subject": subject,
            "body": body,
            "created_by": (agent_context or {}).get("agent_id", "agent"),
            "created_at": _now_iso(),
        },
        source=(agent_context or {}).get("agent_id", "agent"),
    )
    try:
        activity_id = onto.upsert_object(activity)
    except Exception as e:
        return {"success": False, "error": f"activity insert failed: {e}"}

    return {
        "success": True,
        "activity_id": activity_id,
        "lead_id": lead_id,
        "kind": kind,
        "backend": "ontology",
    }
