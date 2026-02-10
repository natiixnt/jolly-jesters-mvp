FROM node:20-bullseye-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY package.json pnpm-lock.yaml tsconfig.json tsdown.config.ts oxlint.json ./

RUN npm install -g pnpm@10.28.2 \
    && pnpm install --frozen-lockfile

COPY src ./src

RUN mkdir -p /app/lib

EXPOSE 3000

CMD ["pnpm", "start"]
