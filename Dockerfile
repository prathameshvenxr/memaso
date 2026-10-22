# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
ARG EXTRAS="[fastembed]"
COPY pyproject.toml README.md ./
COPY memory_core ./memory_core
RUN --mount=type=cache,target=/root/.cache/pip pip install --default-timeout=120 --retries=10 ".${EXTRAS}"
RUN useradd -r -m -u 10001 memaso && mkdir -p /data/models && chown -R memaso /data
ENV HF_HOME=/data/models/hf FASTEMBED_CACHE_PATH=/data/models
USER memaso
EXPOSE 8080
CMD ["python", "-m", "memory_core", "serve", "--host", "0.0.0.0", "--port", "8080"]
