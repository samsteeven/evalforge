import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, CreatedAtMixin, IdMixin


class ModelConfiguration(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "model_configurations"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    provider_kind: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(500))
    model_name: Mapped[str] = mapped_column(String(200))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Prix saisi par l'utilisateur, avec sa source : sans prix, le coût reste inconnu plutôt qu'inventé.
    pricing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
