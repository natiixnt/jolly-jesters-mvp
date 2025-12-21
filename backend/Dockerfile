# uzywamy pelnego obrazu debian  aby moc zainstalowac chrome
FROM python:3.11-bookworm

WORKDIR /app

# krok 1: instalacja podstawowych zaleznosci
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    git \
    postgresql-client \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# krok 2: dodanie repo google chrome (tylko dla amd64)
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
      && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list; \
    else \
      echo "Pominieto repo Google Chrome dla arch $(dpkg --print-architecture)"; \
    fi

# krok 3: instalacja przegladarki (chrome dla amd64, chromium dla pozostalych)
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      apt-get update && apt-get install -y \
        google-chrome-stable \
        libvulkan1 \
        libnss3 \
        fonts-liberation \
        --no-install-recommends; \
    else \
      apt-get update && apt-get install -y \
        chromium \
        chromium-driver \
        libvulkan1 \
        libnss3 \
        fonts-liberation \
        --no-install-recommends; \
    fi \
    && rm -rf /var/lib/apt/lists/*


# krok 4: skopiowanie i instalacja zaleznosci pythona
COPY requirements.txt .
# usunieto: COPY vendor ./vendor (juz niepotrzebne)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir python-dotenv alembic psycopg2-binary

# usunieto: linie zwiazane z antibot.exe (juz niepotrzebne)
  
# kopiowanie kodu i migracji
COPY alembic.ini .
COPY alembic ./alembic
COPY ./app ./app

# expose fastapi port
EXPOSE 8000

# ustawienie pythonpath
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ta komenda cmd jest dla backendu, worker nadpisze ja w docker-compose
CMD ["sh", "-c", "\
cd /app && \
until pg_isready -h postgres -p 5432; do echo 'Waiting for Postgres...'; sleep 2; done; \
alembic -c alembic.ini upgrade head; \
uvicorn app.main:app --host 0.0.0.0 --port 8000"]
