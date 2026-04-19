FROM node:20-bullseye-slim

WORKDIR /app

# System deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    fonts-liberation xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY package.json pnpm-lock.yaml tsconfig.json tsdown.config.ts oxlint.json ./

RUN npm install -g pnpm@10.28.2 \
    && pnpm install --frozen-lockfile

# Install Playwright Firefox (better TLS fingerprint, harder for DataDome to detect)
RUN npx playwright install --with-deps firefox 2>/dev/null || true

COPY src ./src

RUN mkdir -p /app/lib

EXPOSE 3000

CMD ["pnpm", "start"]
