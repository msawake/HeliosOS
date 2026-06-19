"""Intelligence (ontology-backed BI) endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app``
factory). Paths, response shapes, and status codes are the contract and are
preserved exactly. Platform singletons (``ontology``, ``admin_invoker``,
``admin_registry``) come from the process-global di.AppContext instead of
factory closures; the async ``admin_invoker.invoke(...)`` call is driven from
these sync DRF views via ``asgiref.async_to_sync``.

None of these four FastAPI routes used ``Depends(require_role(...))`` — they
were either unauthenticated (``ask``, ontology reads) or used the plain
``check_auth`` authentication dependency (``connectors/sync``). Authentication is
handled by the global ForgeOSAuthentication + IsAuthenticatedOrPublicPath
defaults, so no per-view permission_classes are set.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Session state (ported from fastapi_app.py).
#
# The FastAPI factory held ``_intel_sessions: dict[str, list[dict]] = {}``
# (fastapi_app:540) as a process-local conversation buffer. Preserved here as a
# module-level dict to match behavior. NOTE: this is per-process in-memory state;
# under multiple workers it is not shared — same caveat as the FastAPI original.
# --------------------------------------------------------------------------- #
_intel_sessions: dict[str, list[dict]] = {}


# --------------------------------------------------------------------------- #
# Factory-local helper (ported from fastapi_app:2516).
# --------------------------------------------------------------------------- #
def _intel_fallback(question: str, onto) -> str:
    q = question.lower()
    try:
        if any(kw in q for kw in ["schema", "types", "ontology", "what data"]):
            schema = onto.get_schema()
            types = [t["name"] for t in schema.get("types", [])]
            links = [l["name"] for l in schema.get("link_types", [])]
            return f"**Ontology Schema:**\nTypes: {', '.join(types)}\nRelationships: {', '.join(links)}"
        if any(kw in q for kw in ["customer", "client"]):
            objs = onto.query_objects("Customer", limit=10)
            if not objs:
                return "No customers in ontology yet. Upload data via CSV connector."
            lines = [f"**{len(objs)} Customers:**"]
            for o in objs:
                lines.append(f"  - {o.properties.get('name', '?')} ({o.properties.get('stage', '?')})")
            return "\n".join(lines)
        return f"Ontology has {len(onto.get_types())} types and {len(onto.get_link_types())} relationships. Ask about specific types or upload data."
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


# --------------------------------------------------------------------------- #
# Serializers (request bodies; response bodies are returned as plain dicts to
# preserve the exact FastAPI contract).
# --------------------------------------------------------------------------- #
class IntelligenceRequestSerializer(serializers.Serializer):
    question = serializers.CharField()
    session_id = serializers.CharField(required=False, default="default")


# --------------------------------------------------------------------------- #
# POST /api/intelligence/ask
# --------------------------------------------------------------------------- #
class IntelligenceAskView(APIView):
    def post(self, request):
        ctx = di.get_context()
        ontology = ctx.ontology
        if not ontology:
            return Response({"detail": "Intelligence platform not enabled"}, status=404)

        ser = IntelligenceRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        question = ser.validated_data["question"]
        sid = ser.validated_data["session_id"]

        if sid not in _intel_sessions:
            _intel_sessions[sid] = []
        history = _intel_sessions[sid]
        history.append({"role": "user", "content": question})

        admin_invoker = ctx.admin_invoker
        admin_registry = ctx.admin_registry

        # Try intel-analyst agent
        if admin_invoker and admin_registry:
            try:
                cfg = admin_registry.get("intel-analyst")
                if cfg:
                    result = async_to_sync(admin_invoker.invoke)("intel-analyst", question)
                    if result.result:
                        history.append({"role": "assistant", "content": result.result})
                        return Response({
                            "response": result.result,
                            "session_id": sid,
                            "turns": len(history) // 2,
                        })
            except Exception as e:  # noqa: BLE001
                logger.warning("Intel agent failed: %s", e)

        # Fallback: direct ontology query
        resp = _intel_fallback(question, ontology)
        history.append({"role": "assistant", "content": resp})
        return Response({
            "response": resp,
            "session_id": sid,
            "turns": len(history) // 2,
        })


# --------------------------------------------------------------------------- #
# GET /api/intelligence/ontology/schema
# --------------------------------------------------------------------------- #
class OntologySchemaView(APIView):
    def get(self, request):
        ctx = di.get_context()
        ontology = ctx.ontology
        if not ontology:
            return Response({"detail": "Intelligence not enabled"}, status=404)
        return Response(ontology.get_schema())


# --------------------------------------------------------------------------- #
# GET /api/intelligence/ontology/objects
# --------------------------------------------------------------------------- #
class OntologyObjectsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        ontology = ctx.ontology
        if not ontology:
            return Response({"detail": "Intelligence not enabled"}, status=404)

        type_name = request.query_params.get("type")
        if not type_name:
            # FastAPI declared `type: str = Query(..., alias="type")` (required).
            return Response({"detail": "Field required: type"}, status=422)
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            return Response({"detail": "limit must be an integer"}, status=422)

        objects = ontology.query_objects(type_name, limit=limit)
        return Response([
            {"id": o.id, "type": o.type_name, "properties": o.properties,
             "source": o.source, "created_at": o.created_at}
            for o in objects
        ])


# --------------------------------------------------------------------------- #
# POST /api/intelligence/connectors/sync
# --------------------------------------------------------------------------- #
class ConnectorsSyncView(APIView):
    def post(self, request):
        # FastAPI: status_code=202, plain static body (no platform calls).
        return Response(
            {"status": "accepted", "message": "Sync triggered (background)"},
            status=202,
        )
