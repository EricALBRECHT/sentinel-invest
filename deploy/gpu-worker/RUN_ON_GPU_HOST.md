# Run on sentinel-gpu-01 (DESKTOP / WSL as user eric)

Core has no inbound SSH to the GPU PC. Run these commands locally on the GPU host:

```bash
cd ~/sentinel   # or your clone path
git pull
cd deploy/gpu-worker
./deploy-ai.sh
```

This will:
1. Download `qwen2.5-1.5b-instruct-q4_k_m.gguf` into `/home/eric/sentinel-models`
2. Symlink `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`
3. Rebuild the CUDA llama-cpp image (sm_61)
4. Restart `sentinel-gpu-worker`
5. Run `./test-model.sh`

Then on core, force re-analyse NVDA document 3:

```bash
curl -X POST "http://192.168.1.116:8000/admin/jobs/ai-document/3?force=true" \
  -H "Authorization: Bearer $TOKEN"
```
