from pydantic import BaseModel


class GpuProbeRead(BaseModel):
    worker: str | None = None
    gpu_available: bool | None = None
    gpu_name: str | None = None
    gpu_memory_total_mb: int | None = None
    gpu_memory_free_mb: int | None = None
    cuda_visible: bool | None = None
    timestamp: str | None = None


class GpuWorkerRead(BaseModel):
    name: str
    hostname: str | None = None
    gpu_name: str | None = None
    gpu_memory_total: int | None = None
    status: str
    last_heartbeat: str | None = None
    version: str | None = None
    online: bool
    probe: GpuProbeRead | None = None
