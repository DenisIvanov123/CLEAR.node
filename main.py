import os
import asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager

import logger
from database import init_db
from routers import sync, admin_users, admin_groups, admin_system, auth, client_logs
from ws_router import router as ws_router
from tasks import background_cleanup_task

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    cleanup_task = asyncio.create_task(background_cleanup_task())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="CLEAR.node", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(req: Request):
    path = req.url.path.rstrip("/")
    base = path[:-5] if path.endswith("/docs") else ""
    openapi_url = f"{base}/openapi.json" if base else "/openapi.json"
    return get_swagger_ui_html(
        openapi_url=openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )

@app.get("/ping")
@app.get("/health")
def ping():
    return {"status": "ok"}

app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(admin_users.router)
app.include_router(admin_groups.router)
app.include_router(admin_system.router)
app.include_router(client_logs.router)
app.include_router(ws_router)

web_dir = os.path.join(os.path.dirname(__file__), "web")
if os.path.isdir(web_dir):
    app.mount("/web", StaticFiles(directory=web_dir, html=True), name="web")

@app.get("/")
def root():
    if os.path.isdir(web_dir):
        return RedirectResponse(url="/web/")
    return {"message": "CLEAR.node API is online"}

if __name__ == "__main__":
    app_host = os.getenv("HOST", "0.0.0.0")
    app_port = int(os.getenv("PORT", 8001))
    
    uvicorn.run("main:app", host=app_host, port=app_port, reload=False)
