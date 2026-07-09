# Frontend Dockerfile — Multi-stage: Node.js build → Nginx serve

# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /build

# Set VITE_API_BASE_URL to empty string so the frontend uses
# same-origin requests (Nginx proxies /api/ → backend container).
ARG VITE_BASE_PATH=/
ARG VITE_API_BASE_PATH=
ARG VITE_API_BASE_URL=

ENV VITE_BASE_PATH=$VITE_BASE_PATH
ENV VITE_API_BASE_PATH=$VITE_API_BASE_PATH
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2: Serve
FROM nginx:1.27-alpine

# Replace default config with our custom one
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built frontend assets
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8080/ > /dev/null || exit 1
