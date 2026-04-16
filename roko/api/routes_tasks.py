"""Task management API routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.models import ScheduleConfig, TaskConfig, TaskOptions
from ..scheduler.models import TaskStatus
from .deps import app_state

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    name: str
    enabled: bool = True
    schedule: ScheduleConfig
    options: TaskOptions = TaskOptions()
    commands: List[Dict[str, Any]] = []
    command_file: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[ScheduleConfig] = None
    options: Optional[TaskOptions] = None
    commands: Optional[List[Dict[str, Any]]] = None
    command_file: Optional[str] = None


@router.get("", response_model=List[Dict[str, Any]])
def list_tasks():
    """List all tasks with their config and status."""
    return app_state.task_manager.list_task_details()


@router.post("", response_model=Dict[str, Any])
def create_task(req: TaskCreateRequest):
    """Create a new task."""
    config = TaskConfig(**req.model_dump())
    try:
        status = app_state.task_manager.add_task(config)
        return {"status": status.model_dump(), "message": f"Task '{config.name}' created"}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{name}", response_model=Dict[str, Any])
def get_task(name: str):
    """Get task details and status."""
    try:
        config = app_state.task_manager.get_task_config(name)
        status = app_state.task_manager.get_task_status(name)
        return {"config": config.model_dump(), "status": status.model_dump()}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.put("/{name}", response_model=Dict[str, Any])
def update_task(name: str, req: TaskUpdateRequest):
    """Update task configuration."""
    try:
        existing = app_state.task_manager.get_task_config(name)
        update_data = req.model_dump(exclude_none=True)
        merged = existing.model_dump()
        merged.update(update_data)
        new_config = TaskConfig(**merged)
        status = app_state.task_manager.update_task(name, new_config)
        return {"status": status.model_dump(), "message": f"Task '{name}' updated"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.delete("/{name}")
def delete_task(name: str):
    """Remove a task (stops it if running)."""
    try:
        app_state.task_manager.remove_task(name)
        return {"message": f"Task '{name}' removed"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.post("/{name}/start", response_model=Dict[str, Any])
def start_task(name: str):
    try:
        status = app_state.task_manager.start_task(name)
        return {"status": status.model_dump(), "message": f"Task '{name}' started"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.post("/{name}/stop", response_model=Dict[str, Any])
def stop_task(name: str):
    try:
        status = app_state.task_manager.stop_task(name)
        return {"status": status.model_dump(), "message": f"Task '{name}' stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.post("/{name}/pause", response_model=Dict[str, Any])
def pause_task(name: str):
    try:
        status = app_state.task_manager.pause_task(name)
        return {"status": status.model_dump(), "message": f"Task '{name}' paused"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.post("/{name}/resume", response_model=Dict[str, Any])
def resume_task(name: str):
    try:
        status = app_state.task_manager.resume_task(name)
        return {"status": status.model_dump(), "message": f"Task '{name}' resumed"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")


@router.post("/{name}/trigger", response_model=Dict[str, Any])
def trigger_task(name: str):
    """Trigger one immediate cycle regardless of schedule."""
    try:
        status = app_state.task_manager.trigger_task(name)
        return {"status": status.model_dump(), "message": f"Task '{name}' triggered"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")
