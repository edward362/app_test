# api/routes.py
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

@router.get("/")
async def index():
    """
    Serve the main trading game HTML page.
    Equivalent to your old INDEX_HTML-based route.
    """
    file_path = Path(__file__).parent.parent / "ui" / "index.html"
    return FileResponse(
        file_path,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
