"""GET /api/service/{train_id} — full service drilldown."""

from fastapi import APIRouter, HTTPException, Request

from aggregation import aggregate_service, build_event_list

router = APIRouter()


@router.get("/api/service/{train_id}")
def get_service(train_id: str, request: Request):
    store = request.app.state.store
    ref = request.app.state.ref

    messages = store.get_messages(train_id)
    if not messages:
        raise HTTPException(status_code=404, detail=f"No messages found for train_id {train_id!r}")

    svc = aggregate_service(train_id, messages, ref)
    events = build_event_list(messages, ref)

    return {
        "train_id": svc["train_id"],
        "headcode": svc["headcode"],
        "toc_id": svc["toc_id"],
        "operator": svc["operator"],
        "summary": {
            "worst_delay_mins": svc["worst_delay_mins"],
            "latest_delay_mins": svc["latest_delay_mins"],
            "trend": svc["trend"],
            "variation_status": svc["variation_status"],
            "cancelled": svc["cancelled"],
            "terminated": svc["terminated"],
            "off_route": svc["off_route"],
            "message_count": svc["message_count"],
            "qualifying_message_count": svc["qualifying_message_count"],
            "last_seen": svc["last_seen"],
        },
        "events": events,
    }
