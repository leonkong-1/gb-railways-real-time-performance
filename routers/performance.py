"""GET /api/performance — service-grain leaderboard.
   GET /api/tocs — all distinct TOC IDs currently in the store."""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from aggregation import aggregate_service

router = APIRouter()


@router.get("/api/performance")
def get_performance(
    request: Request,
    toc_id: str | None = Query(default=None),
    min_delay: int = Query(default=3, ge=0),
    limit: int = Query(default=20, ge=1, le=5000),
    include_terminated: bool = Query(default=False),
    include_cancelled: bool = Query(default=True),
):
    store = request.app.state.store
    ref = request.app.state.ref
    window_config = request.app.state.window_config

    # Snapshot once; release lock before computation.
    snapshot = store.snapshot()

    results = []
    for tid, messages in snapshot.items():
        svc = aggregate_service(tid, messages, ref)
        results.append(svc)

    # Filters.
    if not include_terminated:
        results = [s for s in results if not s["terminated"]]
    if not include_cancelled:
        results = [s for s in results if not s["cancelled"]]
    if toc_id is not None:
        results = [s for s in results if s["toc_id"] == toc_id]

    # Exclude off-route from the delay leaderboard; they appear in store_summary only.
    off_route_results = [s for s in results if s["off_route"]]
    leaderboard_results = [s for s in results if not s["off_route"]]

    # Apply min_delay filter to leaderboard (off-route excluded regardless).
    if min_delay > 0:
        leaderboard_results = [
            s for s in leaderboard_results
            if s["worst_delay_mins"] is not None and s["worst_delay_mins"] >= min_delay
        ]

    # Sort: worst_delay descending (nulls last), then last_seen descending.
    leaderboard_results.sort(
        key=lambda s: (s["worst_delay_mins"] is None, -(s["worst_delay_mins"] or 0), s["last_seen"]),
        reverse=False,
    )

    leaderboard_results = leaderboard_results[:limit]

    # Summary stats from the full (pre-filter) snapshot for context.
    all_services = list(snapshot.keys())
    all_svc_objects = [aggregate_service(tid, snapshot[tid], ref) for tid in all_services]
    delayed_count = sum(
        1 for s in all_svc_objects
        if s["worst_delay_mins"] is not None and s["worst_delay_mins"] >= min_delay and not s["off_route"]
    )
    cancelled_count = sum(1 for s in all_svc_objects if s["cancelled"])
    off_route_count = sum(1 for s in all_svc_objects if s["off_route"])

    return {
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_config": window_config,
        "store_summary": {
            "total_services": len(all_services),
            "delayed_count": delayed_count,
            "cancelled_count": cancelled_count,
            "off_route_count": off_route_count,
            "ref_data_loaded": {
                "toc_ref": ref.toc_ref_loaded,
                "stanox_ref": ref.stanox_ref_loaded,
            },
        },
        "results": leaderboard_results,
        "off_route_services": off_route_results,
    }


@router.get("/api/tocs")
def get_tocs(request: Request):
    """Return all distinct TOC IDs + names currently in the store.

    Populated from ALL services regardless of delay status, so the TOC dropdown
    always shows every operator currently represented — not just those with delayed
    services at the current min_delay threshold.
    """
    store = request.app.state.store
    ref = request.app.state.ref

    snapshot = store.snapshot()

    toc_map: dict[str, str] = {}
    for tid, messages in snapshot.items():
        svc = aggregate_service(tid, messages, ref)
        toc_id = svc.get("toc_id")
        if toc_id:
            toc_map[toc_id] = svc.get("operator") or toc_id

    tocs = sorted(
        [{"toc_id": k, "toc_name": v} for k, v in toc_map.items()],
        key=lambda x: x["toc_name"],
    )
    return {"tocs": tocs}
