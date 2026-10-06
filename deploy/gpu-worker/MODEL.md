# Qwen2.5-1.5B-Instruct GGUF for Sentinel GPU worker

## Source (official)
- Repository: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
- Official file: `qwen2.5-1.5b-instruct-q4_k_m.gguf`
- Sentinel alias (symlink): `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`
- Size: **1117320736** bytes (~1.04 GiB)
- SHA256: **6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e**
- License: **Apache-2.0** (LICENSE in the Hugging Face repo)

## Host path
```text
AI_MODEL_CACHE=/home/eric/sentinel-models
container mount: /models
```

## Fetch
```bash
cd deploy/gpu-worker
./fetch-model.sh
# verifies size + prints sha256
```

## CUDA note (GTX 1060 / Pascal sm_61)
The GPU Dockerfile builds `llama-cpp-python` with:
```text
CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61"
```
Never use a silent CPU wheel for production inference.
