"""Screen monitoring API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from .deps import app_state

router = APIRouter(prefix="/api/screen", tags=["screen"])


@router.get("")
def capture_screen(
    format: str = Query("png", description="Output format: png, jpeg, or base64"),
    region: Optional[str] = Query(None, description="Capture region: left,top,width,height"),
):
    """Capture a screenshot of the primary monitor."""
    if app_state.screen_capture is None:
        raise HTTPException(status_code=503, detail="Screen capture not available")

    parsed_region = None
    if region:
        try:
            parts = [int(x.strip()) for x in region.split(",")]
            if len(parts) != 4:
                raise ValueError
            parsed_region = tuple(parts)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400,
                                detail="region must be 'left,top,width,height' (4 integers)")

    try:
        if format == "base64":
            data = app_state.screen_capture.capture_base64(region=parsed_region)
            return JSONResponse(content={"image": data, "format": "png"})
        elif format in ("png", "jpeg"):
            data = app_state.screen_capture.capture(region=parsed_region, format=format)
            media_type = f"image/{format}"
            return Response(content=data, media_type=media_type)
        else:
            raise HTTPException(status_code=400,
                                detail=f"Unsupported format: {format!r} (use png, jpeg, or base64)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screen capture failed: {e}")
