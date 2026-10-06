# Multi-stage CUDA build for Pascal GTX 1060 (sm_61).
# Builder uses CUDA 11.8 devel so llama-cpp-python compiles with GGML_CUDA.
# Runtime keeps only the installed Python packages + CUDA runtime libs.

FROM nvidia/cuda:11.8.0-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61" \
    FORCE_CMAKE=1 \
    SENTINEL_LLAMA_CUDA_BUILD=1 \
    SENTINEL_CUDA_ARCHS=61

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        build-essential \
        cmake \
        ninja-build \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY deploy/gpu-worker/requirements.txt /build/requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel \
    && pip3 install --no-cache-dir -r /build/requirements.txt \
    && pip3 install --no-cache-dir --force-reinstall --no-binary=llama-cpp-python \
        "llama-cpp-python==0.3.2"


FROM nvidia/cuda:11.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    SENTINEL_LLAMA_CUDA_BUILD=1 \
    SENTINEL_CUDA_ARCHS=61 \
    AI_PROVIDER=local \
    AI_MODEL_NAME=Qwen2.5-1.5B-Instruct-Q4_K_M \
    AI_MODEL_CACHE=/models \
    AI_GPU_LAYERS=20 \
    AI_MAX_CONTEXT=2048 \
    AI_MAX_OUTPUT_TOKENS=768 \
    AI_TEMPERATURE=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
COPY app/jobs/gpu /app/app/jobs/gpu
RUN touch /app/app/__init__.py /app/app/jobs/__init__.py

CMD ["python3", "-m", "app.jobs.gpu.worker"]
