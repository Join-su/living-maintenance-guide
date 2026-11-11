# 정비자 앱(8081) · 상급자 앱(8082) 화면 = 한 nginx. 빌드 context = services/app(compose web 줄).
# 1단 node: 같은 package-lock.json으로 설치 → 두 앱 빌드(dist) · 2단 nginx: dist와 nginx.conf만(/api는 api:8000으로 중계).
ARG NODE_IMAGE=node:24-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps ./apps
COPY tools ./tools
COPY e2e/package.json ./e2e/package.json
RUN npm ci --no-audit --no-fund && npm run build

FROM nginx:latest
COPY infra/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /src/apps/tech/dist /srv/tech
COPY --from=build /src/apps/lead/dist /srv/lead
EXPOSE 8081 8082
