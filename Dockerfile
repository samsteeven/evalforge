# syntax=docker/dockerfile:1.7
# Image backend : API, worker, migrations, agent de démonstration et CLI, chacun avec sa commande.
#   docker build -t evalforge-backend .
#   docker run evalforge-backend                                   # API sur 8000
#   docker run evalforge-backend evalforge-worker
#   docker run evalforge-backend alembic -c apps/api/alembic.ini upgrade head
#
# Compatible OpenShift : aucun besoin de root, et OpenShift peut imposer n'importe quel UID (groupe 0) :
# les fichiers appartiennent au groupe 0 avec les mêmes droits que leur propriétaire.

ARG PYTHON_IMAGE=python:3.13.13-slim-trixie

FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dépendances d'abord (couche réutilisée tant que le verrou ne change pas), puis le code des paquets.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
COPY apps/support_agent/pyproject.toml apps/support_agent/pyproject.toml
COPY packages/evaluation_engine/pyproject.toml packages/evaluation_engine/pyproject.toml
COPY packages/llm_gateway/pyproject.toml packages/llm_gateway/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
# Correctifs de sécurité Debian publiés depuis l'image de base (Trivy en relevait 61, dont 3 critiques).
RUN apt-get update     && apt-get upgrade -y --no-install-recommends     && rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.title="evalforge-backend" \
      org.opencontainers.image.source="https://github.com/samsteeven/evalforge" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations et faux fournisseur de démonstration : lus depuis les sources, pas depuis le paquet installé.
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/migrations apps/api/migrations
COPY scripts/fake_llm.py scripts/fake_llm.py
COPY examples examples
RUN useradd --uid 10001 --gid 0 --no-create-home --home-dir /tmp evalforge \
    && chgrp -R 0 /app && chmod -R g=u /app
USER 10001
EXPOSE 8000
# Derrière un proxy (Ingress, Route, nginx du front) : FORWARDED_ALLOW_IPS indique à qui faire confiance.
CMD ["uvicorn", "evalforge_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
