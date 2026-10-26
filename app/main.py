import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.db import create_db
from app.release import scheduler_loop
from app.routers import auth, drops, fans, public, webhooks

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()
    task = asyncio.create_task(scheduler_loop()) if config.SCHEDULER_ENABLED else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="DropKit",
    description="A small Laylo style drop API: drop pages, RSVPs, and release notifications.",
    version="0.1.0",
    lifespan=lifespan,
)

# Loosen or tighten this once you have a frontend URL.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(drops.router)
app.include_router(fans.router)
app.include_router(public.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}
