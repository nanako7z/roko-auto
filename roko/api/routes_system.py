"""System status and health check API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .deps import app_state

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/status")
def system_status():
    tasks = app_state.task_manager.list_tasks() if app_state.task_manager else []
    running_count = sum(1 for t in tasks if t.state == "running")
    uptime = (datetime.now() - app_state.started_at).total_seconds()

    return {
        "driver_type": app_state.driver_type,
        "total_tasks": len(tasks),
        "running_tasks": running_count,
        "uptime_seconds": round(uptime, 1),
        "started_at": app_state.started_at.isoformat(),
    }


@router.post("/shutdown")
def shutdown():
    """Graceful shutdown — stop all tasks."""
    if app_state.task_manager:
        app_state.task_manager.stop_all()
    return {"message": "Shutdown initiated, all tasks stopped"}


class YamlParseRequest(BaseModel):
    content: str


@router.post("/parse-yaml")
def parse_yaml(req: YamlParseRequest) -> Dict[str, Any]:
    """Parse YAML string and return as JSON. Useful for the web UI."""
    try:
        data = yaml.safe_load(req.content)
        return {"ok": True, "data": data}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
