@app.get("/")
async def index():
  return HTMLResponse(INDEX_HTML,
                      headers={
                          "Cache-Control":
                          "no-cache, no-store, must-revalidate",
                          "Pragma": "no-cache",
                          "Expires": "0"
                      })
