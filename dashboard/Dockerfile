# Next.js production image (standalone). Build from repository root:
#   docker build -f dashboard/Dockerfile -t forgeos-web:latest .
#
# SKIP_API_REWRITE=1: assume Ingress routes /api to the API service (same browser origin).
# INTERNAL_API_URL: used only for server-side fetch in dashboard (SSR) if any.

FROM node:22-alpine AS deps
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY dashboard/ ./
ENV NEXT_TELEMETRY_DISABLED=1
ENV INTERNAL_API_URL=http://forgeos-api.forgeos.svc.cluster.local:5000
ARG FORGEOS_API_URL=http://127.0.0.1:5000
ENV FORGEOS_API_URL=$FORGEOS_API_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
