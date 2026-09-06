# syntax=docker/dockerfile:1

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# The pages are prerendered here, so the API has to be reachable at build time
# or the static output ships with an empty city list.
ARG API_BASE_URL=http://api:8000/api/v1
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ENV API_BASE_URL=$API_BASE_URL \
    NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

RUN addgroup -g 10001 -S clearway && adduser -S -u 10001 -G clearway clearway

COPY --from=builder /app/public ./public
COPY --from=builder --chown=clearway:clearway /app/.next/standalone ./
COPY --from=builder --chown=clearway:clearway /app/.next/static ./.next/static

USER clearway
EXPOSE 3000
CMD ["node", "server.js"]
