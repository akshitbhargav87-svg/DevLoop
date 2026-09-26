from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if __package__ == "backend":
    from database import Base, engine
    from routers.reports import router as reports_router
    from routers.runs import router as runs_router
else:  # Support the documented `cd backend; uvicorn main:app` launch.
    from database import Base, engine
    from routers.reports import router as reports_router
    from routers.runs import router as runs_router


app = FastAPI(
    title="DevLoop API",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router)
app.include_router(reports_router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}
