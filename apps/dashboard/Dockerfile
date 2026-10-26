# Build static assets; gateway URL injected at image build for the target env.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# Empty = same-origin API via dashboard nginx proxy (required for Kind so the
# session cookie is first-party on dashboard.urumi.local). Override for prod
# only when the SPA and gateway share a deliberate cross-origin setup.
ARG VITE_GATEWAY_API_URL=
ENV VITE_GATEWAY_API_URL=$VITE_GATEWAY_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
