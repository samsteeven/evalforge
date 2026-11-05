"""Contrats des événements publiés sur RabbitMQ.

Versionnés : un consommateur refuse une version qu'il ne connaît pas.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from evalforge_api.core.database import utcnow

EVALUATION_REQUESTED = "evaluation.requested"
EVALUATION_COMPLETED = "evaluation.completed"
EVALUATION_FAILED = "evaluation.failed"


class Envelope(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    schema_version: int = 1
    occurred_at: datetime = Field(default_factory=utcnow)
    # Relie tous les événements d'un même run (journaux, traces).
    correlation_id: str
    payload: dict[str, Any]
    # Contexte W3C (traceparent) de la requête qui a produit l'événement : le worker continue la même trace,
    # même si l'événement attend dans l'outbox ou dans une file de reprise.
    trace_context: dict[str, str] = Field(default_factory=dict)


class EvaluationRequested(BaseModel):
    run_id: uuid.UUID
    experiment_id: uuid.UUID
    dataset_version_id: uuid.UUID
    workspace_id: uuid.UUID
