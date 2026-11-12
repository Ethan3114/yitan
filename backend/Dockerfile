FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/app/runtime TMPDIR=/app/runtime/tmp
WORKDIR /app
RUN mkdir -p /app/runtime/tmp
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 yitan
COPY --chown=yitan:yitan app ./app
RUN chown -R yitan:yitan /app/runtime
USER yitan
EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn app.main:app_factory --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --no-proxy-headers"]
