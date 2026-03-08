"""GET /api/health"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

router = APIRouter()

STALE_THRESHOLD_SECONDS = 60


@router.get("/api/health")
def get_health(request: Request):
    store = request.app.state.store
    consumer = request.app.state.consumer
    ref = request.app.state.ref

    consumer_running = consumer.is_alive() and consumer.is_consuming
    last_received = store.last_message_received_at

    # Determine staleness.
    feed_stale = True
    if last_received:
        try:
            then = datetime.fromisoformat(last_received.replace("Z", "+00:00"))
            age_s = (datetime.now(tz=timezone.utc) - then).total_seconds()
            feed_stale = age_s > STALE_THRESHOLD_SECONDS
        except Exception:
            pass

    # Phase 1: consumer thread exits after the ingest window closes.
    # Use the consumer's own phase1_complete flag rather than is_alive().
    phase1_complete = getattr(consumer, "phase1_complete", False)

    if phase1_complete:
        status = "phase1_complete"
    elif consumer_running and not feed_stale:
        status = "ok"
    else:
        status = "degraded"

    return {
        "status": status,
        "consumer_running": consumer.is_alive(),
        "consumer_consuming": consumer.is_consuming,
        "phase1_complete": phase1_complete,
        "messages_consumed_total": store.total_consumed,
        "cache_size": store.cache_size,
        "ref_data": {
            "toc_ref_loaded": ref.toc_ref_loaded,
            "stanox_ref_loaded": ref.stanox_ref_loaded,
        },
        "last_message_received_at": last_received,
    }
