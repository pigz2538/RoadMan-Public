# Use the Debian image for deterministic npm/rollup installs. The Alpine
# resolver intermittently exits before writing its npm completion state on
# larger dependency trees.
FROM node:22-slim AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend .
COPY shared /shared
ARG VITE_AMAP_JSAPI_KEY=
ARG VITE_AMAP_SECURITY_JS_CODE=
ARG VITE_AMAP_SERVICE_HOST=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=
ENV VITE_AMAP_JSAPI_KEY=$VITE_AMAP_JSAPI_KEY
ENV VITE_AMAP_SECURITY_JS_CODE=$VITE_AMAP_SECURITY_JS_CODE
ENV VITE_AMAP_SERVICE_HOST=$VITE_AMAP_SERVICE_HOST
ENV HTTP_PROXY=$HTTP_PROXY
ENV HTTPS_PROXY=$HTTPS_PROXY
ENV NO_PROXY=$NO_PROXY
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
