from pydantic import BaseModel


class JobEnqueueRead(BaseModel):
    job_id: str
    queue: str
    enqueued: bool
    status: str


class DueSyncRead(BaseModel):
    selected: int
    enqueued: int
    already_active: int
    job_ids: list[str]


class JobDetailRead(BaseModel):
    job_id: str
    queue: str | None
    status: str | None
    enqueued_at: str | None
    started_at: str | None
    ended_at: str | None
    result: dict | None
    error: str | None
    document_id: int | None = None
    run_id: str | None = None
    force: bool | None = None
    retries_left: int | None = None
    scheduled_at: str | None = None
    previous_error: str | None = None
