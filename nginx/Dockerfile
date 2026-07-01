# Stage 1: build the React app
FROM node:22-alpine AS ui-build
WORKDIR /app

# Vite embeds VITE_* env vars into the bundle at build time. The values
# need to be available BEFORE `npm run build` runs — hence build-args
# (passed via docker-compose `args:`) rather than runtime env. Empty
# defaults so the build still succeeds when a key is unset (the app
# gracefully hides features that require missing config).
ARG VITE_CIRCLE_CLIENT_KEY=""
ARG VITE_CIRCLE_CLIENT_URL=""
ARG VITE_API_BASE=""
ENV VITE_CIRCLE_CLIENT_KEY=${VITE_CIRCLE_CLIENT_KEY}
ENV VITE_CIRCLE_CLIENT_URL=${VITE_CIRCLE_CLIENT_URL}
ENV VITE_API_BASE=${VITE_API_BASE}

COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

# Stage 2: serve with non-root nginx (listens on 8080, no CAP_NET_BIND_SERVICE needed)
# Pinned to a specific version: nginx.conf's upstream `server … resolve` directive
# (runtime DNS re-resolution) REQUIRES nginx >= 1.27.3. An unpinned `:alpine` tag
# (or a stale cached layer) could resolve to an older nginx and make `nginx -t`
# fail at deploy time, taking the proxy down. Pinned to 1.31.2 to MATCH the live
# box (`Server: nginx/1.31.2`) — never downgrade prod. Bump this tag deliberately. (#780)
FROM nginxinc/nginx-unprivileged:1.31.2-alpine
COPY --from=ui-build /app/dist /usr/share/nginx/html
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
