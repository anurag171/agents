FROM python:3.12-slim
# Runs as an arbitrary non-root UID (OpenShift "restricted" SCC): code is read-only, only /data and /tmp are written.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VM_DATA=/data VM_LOG_JSON=1 VM_ROLE=all PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data && chgrp -R 0 /app /data && chmod -R g=u /app /data
USER 1001
EXPOSE 8080
# /data MUST be a persistent volume (it holds the signals log). One replica unless the volume is shared (see TODO.md).
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--timeout", "60", "app:app"]
