FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend .
COPY shared /shared
COPY Skills/amap-jsapi/apikey.txt /Skills/amap-jsapi/apikey.txt
COPY Skills/amap-jsapi/secretkey.txt /Skills/amap-jsapi/secretkey.txt
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
