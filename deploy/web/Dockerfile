FROM node:20-alpine AS build
WORKDIR /web

# 服务器拉取官方 npm 源常不稳定，默认用国内镜像
RUN npm config set registry https://registry.npmmirror.com

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
