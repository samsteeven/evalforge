# syntax=docker/dockerfile:1.7
# Image du front : fichiers statiques servis par nginx sans privilèges (port 8080), /api relayé vers l'API.
#   docker build -t evalforge-frontend frontend
#   docker run -e EVALFORGE_API_UPSTREAM=http://api:8000 -p 8080:8080 evalforge-frontend

FROM node:24-alpine3.22 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY . .
RUN npx vite build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine-slim
LABEL org.opencontainers.image.title="evalforge-frontend" \
      org.opencontainers.image.source="https://github.com/samsteeven/evalforge"
# Même origine pour le navigateur : le cookie de session reste limité à /api/v1/auth, comme en développement.
ENV EVALFORGE_API_UPSTREAM=http://api:8000
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# OpenShift impose un UID quelconque du groupe 0 : l'image de base est déjà prévue pour.
USER 101
EXPOSE 8080
