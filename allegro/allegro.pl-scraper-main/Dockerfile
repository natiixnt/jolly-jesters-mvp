FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache curl

COPY package.json pnpm-lock.yaml tsconfig.json tsdown.config.ts oxlint.json ./

RUN npm install -g pnpm@10.28.2 \
    && pnpm install --frozen-lockfile

COPY src ./src

EXPOSE 3000

CMD ["pnpm", "start"]
