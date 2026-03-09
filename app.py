"""
FastAPI entrypoint.

Start with:
    python app.py
or:
    uvicorn app:app --reload

Phase 1 behaviour: the Kafka consumer ingests for PHASE1_INGEST_SECONDS seconds
(default 120) then stops.  The server stays up; query the API or load the frontend
to inspect the captured dataset.  Set PHASE1_INGEST_SECONDS=0 for continuous
operation (Phase 2).
"""

import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from consumer import KafkaConsumerThread
from reference import ReferenceData
from routers import health, performance, qc, service
from store import MessageStore

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="GB Train Performance Dashboard", version="1.0.0")

# ------------------------------------------------------------------
# Startup / shutdown
# ------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    # --- Configuration ---
    max_msgs = int(os.environ.get("WINDOW_MAX_MESSAGES_PER_SERVICE", "10"))
    max_age = int(os.environ.get("WINDOW_MAX_AGE_MINUTES", "240"))
    max_age_zombie = int(os.environ.get("WINDOW_MAX_AGE_ZOMBIE_MINUTES", "480"))
    ingest_seconds = int(os.environ.get("PHASE1_INGEST_SECONDS", "120"))

    logger.info(
        "Starting with max_messages_per_service=%d, max_age_minutes=%d, "
        "max_age_zombie_minutes=%d, phase1_ingest_seconds=%d (0=continuous).",
        max_msgs, max_age, max_age_zombie, ingest_seconds,
    )

    # --- Reference data ---
    ref = ReferenceData()
    ref.load_all()

    # --- Message store ---
    store = MessageStore(
        max_messages_per_service=max_msgs,
        max_age_minutes=max_age,
        max_age_zombie_minutes=max_age_zombie,
    )
    store.start_sweep(interval_seconds=600)  # background eviction every 10 min

    # --- Kafka consumer thread ---
    consumer = KafkaConsumerThread(store=store, ingest_seconds=ingest_seconds)
    consumer.start()

    if ingest_seconds > 0:
        logger.info(
            "Phase 1 mode: consuming for %ds then stopping. "
            "Set PHASE1_INGEST_SECONDS=0 for continuous operation.",
            ingest_seconds,
        )
    else:
        logger.info("Phase 2 mode: continuous consumption.")

    # --- Attach to app state ---
    app.state.store = store
    app.state.ref = ref
    app.state.consumer = consumer
    app.state.ingest_seconds = ingest_seconds
    app.state.window_config = {
        "max_messages_per_service": max_msgs,
        "max_age_minutes": max_age,
    }


@app.on_event("shutdown")
async def shutdown():
    store = getattr(app.state, "store", None)
    if store:
        store.stop_sweep()
    consumer = getattr(app.state, "consumer", None)
    if consumer:
        consumer.stop()
        consumer.join(timeout=5)
    logger.info("Shutdown complete.")


# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------

app.include_router(health.router)
app.include_router(performance.router)
app.include_router(service.router)
app.include_router(qc.router)


# ------------------------------------------------------------------
# Frontend
# ------------------------------------------------------------------

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")


# ------------------------------------------------------------------
# Dev entrypoint
# ------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
