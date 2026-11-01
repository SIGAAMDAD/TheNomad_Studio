FROM node:22-alpine AS build
WORKDIR /workspace
COPY ui/package.json ui/package-lock.json ./ui/
RUN cd ui && npm ci --ignore-scripts --no-audit --no-fund
COPY CONTRACTS/openapi.json CONTRACTS/api.types.ts ./CONTRACTS/
COPY ui/ ./ui/
RUN cd ui && npm run build

FROM caddy:2.10-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /workspace/ui/dist/ /srv/
