FROM python:3.12-slim

WORKDIR /app
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]


COPY requirements.txt .

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir --upgrade pip --retries 5 --timeout 120 && \
    pip install --no-cache-dir -r requirements.txt --retries 5 --timeout 120

COPY . .

EXPOSE 8501
ENV PYTHONPATH=/app
