"""URL routing for the intelligence app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly).
"""

from django.urls import path

from .views import (
    ConnectorsSyncView,
    IntelligenceAskView,
    OntologyObjectsView,
    OntologySchemaView,
)

urlpatterns = [
    path("api/intelligence/ask", IntelligenceAskView.as_view()),
    path("api/intelligence/connectors/sync", ConnectorsSyncView.as_view()),
    path("api/intelligence/ontology/objects", OntologyObjectsView.as_view()),
    path("api/intelligence/ontology/schema", OntologySchemaView.as_view()),
]
