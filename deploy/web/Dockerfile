FROM node:20-alpine AS build
WORKDIR /web

# 服务器拉取官方 npm 源常不稳定，默认用国内镜像
RUN npm config set registry https://registry.npmmirror.com

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# Docker 构建跳过 vue-tsc（小内存机器上容易卡住/被 OOM）；类型检查留给本地/CI
ENV NODE_OPTIONS=--max-old-space-size=2048
RUN npx vite build

FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
