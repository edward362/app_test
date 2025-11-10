# app/main.py (or just main.py at repo root if that's where your folders live)
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# REST routes
from api.routes import router as api_router

# WebSocket endpoint (this should be the async def from websockets/endpoints.py)
# If your function name is different, change `ws_endpoint` accordingly.
from websockets.endpoints import ws_endpoint

app = FastAPI(title="Multiplayer Trading Game", version="2.0")

# --- CORS (same as before) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Serve UI (index.html) ---
@app.get("/")
async def serve_ui():
    file_path = Path(__file__).parent / "ui" / "index.html"
    # Same no-cache headers you had before
    return FileResponse(
        file_path,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

# --- Static mounts (optional but handy) ---
# If you plan to put images or other assets locally:
images_dir = Path(__file__).parent / "images"
if images_dir.exists():
    app.mount("/images", StaticFiles(directory=images_dir), name="images")

# --- REST API ---
# If you want the API under /api, use prefix="/api"
app.include_router(api_router)

# --- WebSockets ---
# Reuse the exact handler logic you extracted from server.py
app.add_api_websocket_route("/ws", ws_endpoint)

# --- Healthcheck ---
@app.get("/health")
async def health():
    return {"status": "ok"}

# --- Dev runner ---
if __name__ == "__main__":
    import uvicorn
    # change "main:app" to "app.main:app" if you place this under an `app/` package
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
