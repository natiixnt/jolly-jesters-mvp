FROM node:20-bullseye-slim

WORKDIR /app

# System deps + curl-impersonate for Chrome 146 TLS fingerprint
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    fonts-liberation xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install curl-impersonate (Chrome 146 TLS fingerprint - bypasses DataDome)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
      curl -Lo /tmp/curl-imp.tar.gz "https://github.com/lexiforest/curl-impersonate/releases/download/v1.5.5/curl-impersonate-v1.5.5.x86_64-linux-gnu.tar.gz" && \
      mkdir -p /opt/curl-impersonate && \
      tar xzf /tmp/curl-imp.tar.gz -C /opt/curl-impersonate/ && \
      rm /tmp/curl-imp.tar.gz; \
    fi

COPY package.json pnpm-lock.yaml tsconfig.json tsdown.config.ts oxlint.json ./

RUN npm install -g pnpm@10.28.2 \
    && pnpm install --frozen-lockfile

# Install Playwright Firefox (better TLS fingerprint, harder for DataDome to detect)
RUN npx playwright install --with-deps firefox 2>/dev/null || true

COPY src ./src

RUN mkdir -p /app/lib

EXPOSE 3000

CMD ["pnpm", "start"]
