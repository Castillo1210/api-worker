from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import structlog

from app.models.deposit import ProcessDepositRequest
from app.tasks.process_deposit import process_deposit
from app.worker import celery_app

logger = structlog.get_logger()

router = APIRouter(prefix="/process-deposit", tags=["Process"])

class TaskResponse(BaseModel):
    task_id: str
    deposit_id: str
    status: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[dict] = None
    traceback: Optional[str] = None

@router.post("", response_model=TaskResponse)
async def trigger_process(request: ProcessDepositRequest):
    """
    Dispara task de procesamiento asíncrono.
    Usado por API Bridge o para tests manuales
    """

    task = process_deposit.delay(request.deposit_id)

    logger.info("Task encolada", task_id=task.id, deposit_id=request.deposit_id)

    return TaskResponse(
        task_id=task.id,
        deposit_id=request.deposit_id,
        status="queued"
    )

@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """Consulta estado task Celery"""
    result = celery_app.AsyncResult(task_id)

    return TaskStatusResponse(
        task_id=task_id,
        status=result.status,
        result=result.result if result.ready() else None,
        traceback=result.traceback if result.failed() else None
    )