"""Ontology / knowledge-graph ORM models — Phase A, managed=False.

Tables: ontology_types, ontology_objects, ontology_links, ontology_link_types
(003_ontology_tables.sql); knowledge_entries, decision_precedents
(001_schema.sql).
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from pgvector.django import VectorField

from forgeos_web.db import TenantModel


class OntologyType(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    name = models.TextField()
    properties = models.JSONField(default=dict)
    description = models.TextField(default="")
    icon = models.TextField(default="")
    # nullable in schema (DEFAULT NOW(), not NOT NULL); default reflects insert behavior
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ontology_types"


class OntologyObject(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    type_name = models.TextField()
    properties = models.JSONField(default=dict)
    source = models.TextField(default="manual")
    embedding = VectorField(dimensions=1536, null=True)
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ontology_objects"


class OntologyLink(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    from_id = models.UUIDField()
    to_id = models.UUIDField()
    link_type = models.TextField()
    properties = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ontology_links"


class OntologyLinkType(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    name = models.TextField()
    from_type = models.TextField()
    to_type = models.TextField()
    cardinality = models.TextField(
        default="one_to_many",
        choices=[
            ("one_to_one", "one_to_one"),
            ("one_to_many", "one_to_many"),
            ("many_to_many", "many_to_many"),
        ],
    )
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ontology_link_types"


class KnowledgeEntry(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    category = models.TextField(
        choices=[
            ("policy", "policy"),
            ("procedure", "procedure"),
            ("decision", "decision"),
            ("faq", "faq"),
            ("technical", "technical"),
            ("runbook", "runbook"),
        ]
    )
    title = models.TextField()
    content = models.TextField()
    tags = ArrayField(models.TextField(), default=list)
    department = models.TextField(null=True, blank=True)
    created_by = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    embedding = VectorField(dimensions=1536, null=True)

    class Meta:
        managed = False
        db_table = "knowledge_entries"


class DecisionPrecedent(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    title = models.TextField()
    category = models.TextField()
    department = models.TextField()
    decision = models.TextField()
    reasoning = models.TextField()
    made_by = models.TextField()
    outcome = models.TextField(null=True, blank=True)
    outcome_rating = models.TextField(
        null=True,
        blank=True,
        choices=[
            ("positive", "positive"),
            ("neutral", "neutral"),
            ("negative", "negative"),
        ],
    )
    context = models.JSONField(default=dict)
    tags = ArrayField(models.TextField(), default=list)
    superseded_by = models.UUIDField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "decision_precedents"
