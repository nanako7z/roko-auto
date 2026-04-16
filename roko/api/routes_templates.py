"""Template image management API routes — upload, list, delete, test match."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from .deps import app_state

router = APIRouter(prefix="/api/templates", tags=["templates"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name must not be empty")
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Name contains invalid characters")
    return name


def _templates_dir() -> Path:
    d = app_state.templates_dir
    if not d:
        raise HTTPException(status_code=500, detail="templates_dir not configured")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_template(name: str) -> Path:
    """Find template file by name (with or without extension)."""
    d = _templates_dir()
    # Try exact name first
    exact = d / name
    if exact.exists() and exact.suffix.lower() in ALLOWED_EXTENSIONS:
        return exact
    # Try adding extensions
    for ext in ALLOWED_EXTENSIONS:
        candidate = d / (name + ext)
        if candidate.exists():
            return candidate
    raise HTTPException(status_code=404, detail=f"Template '{name}' not found")


@router.get("")
def list_templates() -> List[Dict[str, Any]]:
    """List all template images with metadata."""
    d = _templates_dir()
    results = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            results.append({
                "name": p.stem,
                "filename": p.name,
                "size": p.stat().st_size,
            })
    return results


@router.post("")
async def upload_template(file: UploadFile = File(...),
                          name: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Upload a template image (PNG or JPG)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported format: {ext}. Use PNG or JPG.")

    # Use provided name or derive from filename
    template_name = _validate_name(name) if name else _validate_name(Path(file.filename).stem)

    d = _templates_dir()
    dest = d / (template_name + ext)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    dest.write_bytes(content)
    return {"message": f"Template '{template_name}' uploaded", "filename": dest.name}


@router.get("/{name}")
def get_template(name: str, format: str = Query("image")) -> Response:
    """Get a template image. format=image returns raw, format=base64 returns JSON."""
    name = _validate_name(name)
    path = _find_template(name)

    data = path.read_bytes()
    if format == "base64":
        from fastapi.responses import JSONResponse
        return JSONResponse({
            "name": path.stem,
            "filename": path.name,
            "image": base64.b64encode(data).decode("ascii"),
        })

    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return Response(content=data, media_type=media)


@router.delete("/{name}")
def delete_template(name: str) -> Dict[str, str]:
    """Delete a template image."""
    name = _validate_name(name)
    path = _find_template(name)
    path.unlink()
    return {"message": f"Template '{name}' deleted"}


@router.post("/{name}/test")
def test_template(name: str,
                  threshold: float = Query(0.8, ge=0.0, le=1.0)) -> Dict[str, Any]:
    """Test template matching against the current screen.

    Returns match result and an annotated screenshot (base64).
    """
    name = _validate_name(name)
    path = _find_template(name)

    sc = app_state.screen_capture
    if not sc:
        raise HTTPException(status_code=503, detail="Screen capture not available")

    from ..screen.matcher import TemplateMatcher

    matcher = TemplateMatcher(path, threshold=threshold)
    screenshot = sc.capture(format="png")
    match, annotated_png = matcher.match_annotated(screenshot)

    result: Dict[str, Any] = {
        "matched": match is not None,
        "threshold": threshold,
        "annotated_image": base64.b64encode(annotated_png).decode("ascii"),
    }
    if match:
        result.update({
            "confidence": round(match.confidence, 4),
            "center_x": match.center_x,
            "center_y": match.center_y,
            "x": match.x,
            "y": match.y,
            "width": match.width,
            "height": match.height,
        })
    return result
