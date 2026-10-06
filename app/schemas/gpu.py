from pydantic import BaseModel, ConfigDict


class GpuProbeRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    worker: str | None = None
    gpu_available: bool | None = None
    gpu_name: str | None = None
    gpu_memory_total_mb: int | None = None
    gpu_memory_free_mb: int | None = None
    cuda_visible: bool | None = None
    ai_provider: str | None = None
    model_name: str | None = None
    ai_model_name: str | None = None
    model_loaded: bool | None = None
    model_backend: str | None = None
    gpu_layers: int | None = None
    context_size: int | None = None
    model_memory_mb: int | None = None
    timestamp: str | None = None


class GpuWorkerRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    hostname: str | None = None
    gpu_name: str | None = None
    gpu_memory_total: int | None = None
    status: str
    last_heartbeat: str | None = None
    version: str | None = None
    online: bool
    ai_provider: str | None = None
    model_name: str | None = None
    model_loaded: bool | None = None
    model_backend: str | None = None
    gpu_layers: int | None = None
    context_size: int | None = None
    model_memory_mb: int | None = None
    probe: GpuProbeRead | None = None
