# CUDA 11.8 stays compatible with Pascal (GTX 1060) and with drivers that
# support CUDA 11.8 or newer. The application image does not assume CUDA 13.
FROM nvidia/cuda:11.8.0-base-ubuntu22.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY deploy/gpu-worker/requirements.txt /tmp/requirements-gpu.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements-gpu.txt \
    && rm /tmp/requirements-gpu.txt

COPY app/jobs/gpu /app/app/jobs/gpu
RUN touch /app/app/__init__.py /app/app/jobs/__init__.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

CMD ["python3", "-m", "app.jobs.gpu.worker"]
