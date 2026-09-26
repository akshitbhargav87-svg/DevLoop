import hmac
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request

if __package__ == "backend":
    from config import settings
    from database import Base, engine
    from routers.auth import router as auth_router
    from routers.reports import router as reports_router
    from routers.runs import router as runs_router
else:  # Support the documented `cd backend; uvicorn main:app` launch.
    from config import settings
    from database import Base, engine
    from routers.auth import router as auth_router
    from routers.reports import router as reports_router
    from routers.runs import router as runs_router


app = FastAPI(
    title="DevLoop API",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router)
app.include_router(reports_router)
app.include_router(auth_router)


@app.middleware("http")
async def require_deployment_access(request: Request, call_next):
    access_token = settings.access_token
    path = request.url.path
    if (
        access_token
        and path.startswith("/api/")
        and not path.startswith("/api/auth/")
    ):
        supplied = request.cookies.get("devloop_access", "")
        if not hmac.compare_digest(supplied, access_token):
            return JSONResponse({"detail": "Access code required."}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


static_dir = os.getenv("STATIC_DIR")
if static_dir and Path(static_dir).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
