"""
Service-grain aggregation (Level 2).

All functions take a snapshot list of raw messages for a single train_id and
return structured data.  No FastAPI or Kafka imports here.

Delay calculation
-----------------
Primary:  delay = actual_timestamp_ms - gbtt_timestamp_ms  (GBTT = passenger timetable)
Fallback: if gbtt_timestamp is absent, "0", or unparseable, fall back to planned_timestamp
          (WTT = working timetable).  The QC trace records which reference time was used.

Off-route handling
------------------
Messages where offroute_ind == "true" are excluded from delay metrics.
These services appear as a separate OFF ROUTE category in the API response;
their timetable_variation is meaningless when not following the scheduled path.

Cancelled flag
--------------
Tracks the most recent event type between 0002 (cancellation) and 0005 (reinstatement)
by received_at.  Handles cancel → reinstate → cancel sequences correctly.

Trend threshold
---------------
TREND_THRESHOLD_MINUTES = 2.0  (configurable constant)
IMPROVING: latest_delay < worst_delay - threshold
WORSENING: latest_delay > worst_delay + threshold (but see below)
STABLE:    otherwise

Note: WORSENING via this formula would only trigger if latest > worst, but by
definition latest can't exceed worst unless the trend check is done on a
per-message basis.  In practice this branch catches edge-cases such as a
service where the worst was recorded mid-journey but the latest is still rising.

BST note
--------
During British Summer Time all TRUST timestamps are 1 hour ahead due to a known
TRUST system bug.  Since delay is a *difference* between two equally-affected
timestamps, the arithmetic cancels and delay values remain correct.  However,
absolute time displays on the frontend will be 1 hour ahead during BST.
"""

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TREND_THRESHOLD_MINUTES: float = 2.0  # configurable; see 04_aggregation.md


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _parse_ms_timestamp(raw: Any) -> int | None:
    """Parse a Unix millisecond timestamp string.  Returns None if invalid."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "0":
        return None
    try:
        val = int(s)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _ms_to_iso(ms: int) -> str:
    """Convert Unix milliseconds to ISO 8601 UTC string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sort_key(msg: dict) -> str:
    """Sort key for a message: actual_timestamp if valid, else received_at."""
    actual_ms = _parse_ms_timestamp(msg.get("actual_timestamp"))
    if actual_ms is not None:
        return _ms_to_iso(actual_ms)
    return msg.get("received_at", "")


# ---------------------------------------------------------------------------
# Per-message delay computation
# ---------------------------------------------------------------------------


def compute_delay(msg: dict) -> dict:
    """
    Compute first-principles delay for a single 0003 message.

    Returns a dict with:
        delay_seconds:       int | None
        delay_mins:          float | None
        reference_time_used: "gbtt" | "planned" | None
        skipped_reason:      str | None  (set if delay is not computable)
    """
    actual_ms = _parse_ms_timestamp(msg.get("actual_timestamp"))
    gbtt_ms = _parse_ms_timestamp(msg.get("gbtt_timestamp"))
    planned_ms = _parse_ms_timestamp(msg.get("planned_timestamp"))

    if actual_ms is None:
        return {"delay_seconds": None, "delay_mins": None, "reference_time_used": None,
                "skipped_reason": "actual_timestamp missing or zero"}

    # Off-route messages: exclude from delay metrics.
    if msg.get("offroute_ind") == "true":
        return {"delay_seconds": None, "delay_mins": None, "reference_time_used": None,
                "skipped_reason": "offroute_ind=true — off-route, no valid timetabled reference"}

    if gbtt_ms is not None:
        reference_ms = gbtt_ms
        reference_time_used = "gbtt"
    elif planned_ms is not None:
        reference_ms = planned_ms
        reference_time_used = "planned"
    else:
        return {"delay_seconds": None, "delay_mins": None, "reference_time_used": None,
                "skipped_reason": "both gbtt_timestamp and planned_timestamp missing or zero"}

    delay_seconds = (actual_ms - reference_ms) // 1000
    delay_mins = round(delay_seconds / 60, 1)
    return {
        "delay_seconds": delay_seconds,
        "delay_mins": delay_mins,
        "reference_time_used": reference_time_used,
        "skipped_reason": None,
    }


# ---------------------------------------------------------------------------
# Service-grain aggregation
# ---------------------------------------------------------------------------


def aggregate_service(train_id: str, messages: list[dict], ref) -> dict:
    """
    Compute the service-grain view for a single train_id.

    Parameters
    ----------
    train_id : str
    messages : list[dict]  — snapshot from MessageStore (read-only)
    ref      : ReferenceData instance

    Returns
    -------
    dict matching the service-grain schema from 04_aggregation.md
    """
    if not messages:
        return _empty_service(train_id)

    headcode = train_id[2:6] if len(train_id) >= 6 else train_id

    # Most recent 0003 message for location/operator fields.
    msgs_0003 = [m for m in messages if m.get("msg_type") == "0003"]
    latest_0003 = msgs_0003[-1] if msgs_0003 else None

    toc_id = latest_0003["toc_id"] if latest_0003 else ""
    operator = ref.resolve_toc(toc_id) if toc_id else ""
    loc_stanox = latest_0003.get("loc_stanox", "") if latest_0003 else ""
    last_known_location = ref.resolve_stanox(loc_stanox) if loc_stanox else ""
    variation_status = latest_0003.get("variation_status", "") if latest_0003 else ""

    # Qualifying messages: 0003, delay_monitoring_point=true, not off-route, valid delay.
    qualifying: list[tuple[dict, dict]] = []  # (message, delay_result)
    for m in msgs_0003:
        if m.get("delay_monitoring_point") != "true":
            continue
        dr = compute_delay(m)
        if dr["delay_mins"] is not None:
            qualifying.append((m, dr))

    qualifying_message_count = len(qualifying)

    worst_delay_mins: float | None = None
    latest_delay_mins: float | None = None

    if qualifying:
        worst_delay_mins = max(dr["delay_mins"] for _, dr in qualifying)
        # Latest = qualifying message with the most recent actual_timestamp_ms.
        # Use received_at as a secondary tiebreaker (handles batch messages that
        # share a second-level received_at timestamp).
        def _latest_key(x):
            m, _ = x
            actual_ms = _parse_ms_timestamp(m.get("actual_timestamp"))
            return (actual_ms or 0, m.get("received_at", ""))

        latest_q_msg, latest_q_dr = max(qualifying, key=_latest_key)
        latest_delay_mins = latest_q_dr["delay_mins"]

    # Trend.
    trend = _compute_trend(worst_delay_mins, latest_delay_mins)

    # Terminated flag: any message with train_terminated == "true".
    terminated = any(m.get("train_terminated") == "true" for m in messages)

    # Cancelled flag: most recent event type between 0002 and 0005.
    cancelled = _compute_cancelled(messages)

    # Off-route flag (advisory): any current 0003 with offroute_ind=true.
    off_route = latest_0003 is not None and latest_0003.get("offroute_ind") == "true"

    most_recent = messages[-1]
    last_seen = most_recent.get("received_at", "")

    return {
        "train_id": train_id,
        "headcode": headcode,
        "toc_id": toc_id,
        "operator": operator,
        "last_known_location": last_known_location,
        "worst_delay_mins": worst_delay_mins,
        "latest_delay_mins": latest_delay_mins,
        "trend": trend,
        "variation_status": variation_status,
        "cancelled": cancelled,
        "terminated": terminated,
        "off_route": off_route,
        "message_count": len(messages),
        "qualifying_message_count": qualifying_message_count,
        "last_seen": last_seen,
    }


def _empty_service(train_id: str) -> dict:
    return {
        "train_id": train_id,
        "headcode": train_id[2:6] if len(train_id) >= 6 else train_id,
        "toc_id": "",
        "operator": "",
        "last_known_location": "",
        "worst_delay_mins": None,
        "latest_delay_mins": None,
        "trend": "UNKNOWN",
        "variation_status": "",
        "cancelled": False,
        "terminated": False,
        "off_route": False,
        "message_count": 0,
        "qualifying_message_count": 0,
        "last_seen": "",
    }


def _compute_trend(worst: float | None, latest: float | None) -> str:
    if worst is None or latest is None:
        return "UNKNOWN"
    if latest <= worst - TREND_THRESHOLD_MINUTES:
        return "IMPROVING"
    if latest >= worst + TREND_THRESHOLD_MINUTES:
        return "WORSENING"
    return "STABLE"


def _compute_cancelled(messages: list[dict]) -> bool:
    """
    Track most-recent event type between 0002 and 0005 for this train_id.
    Returns True if the most recent of those two types is a 0002 (cancellation).
    """
    latest_ts = ""
    latest_is_cancelled = False
    for m in messages:
        mt = m.get("msg_type")
        if mt not in ("0002", "0005"):
            continue
        ts = m.get("received_at", "")
        if ts >= latest_ts:
            latest_ts = ts
            latest_is_cancelled = mt == "0002"
    return latest_is_cancelled


# ---------------------------------------------------------------------------
# Service drilldown: stop-by-stop event list
# ---------------------------------------------------------------------------


def build_event_list(messages: list[dict], ref) -> list[dict]:
    """
    Return all messages sorted chronologically for the service drilldown view.
    For 0003 messages, includes first-principles computed_delay_mins.
    """
    sorted_msgs = sorted(messages, key=_sort_key)
    events = []
    for m in sorted_msgs:
        event = _build_event(m, ref)
        events.append(event)
    return events


def _build_event(m: dict, ref) -> dict:
    mt = m.get("msg_type", "")

    actual_ms = _parse_ms_timestamp(m.get("actual_timestamp"))
    gbtt_ms = _parse_ms_timestamp(m.get("gbtt_timestamp"))
    planned_ms = _parse_ms_timestamp(m.get("planned_timestamp"))

    actual_iso = _ms_to_iso(actual_ms) if actual_ms else None
    gbtt_iso = _ms_to_iso(gbtt_ms) if gbtt_ms else None
    planned_iso = _ms_to_iso(planned_ms) if planned_ms else None

    stanox = m.get("loc_stanox", "")
    location = ref.resolve_stanox(stanox) if stanox else ""

    event: dict = {
        "msg_type": mt,
        "received_at": m.get("received_at", ""),
    }

    if mt == "0003":
        dr = compute_delay(m)
        event.update({
            "event_type": m.get("event_type", ""),
            "planned_event_type": m.get("planned_event_type", ""),
            "stanox": stanox,
            "location": location,
            "platform": m.get("platform", ""),
            "actual_timestamp": actual_iso,
            "gbtt_timestamp": gbtt_iso,
            "planned_timestamp": planned_iso,
            "computed_delay_mins": dr["delay_mins"],
            "reference_time_used": dr["reference_time_used"],
            "timetable_variation": m.get("timetable_variation", ""),
            "variation_status": m.get("variation_status", ""),
            "delay_monitoring_point": m.get("delay_monitoring_point") == "true",
            "offroute_ind": m.get("offroute_ind") == "true",
            "train_terminated": m.get("train_terminated") == "true",
            "correction_ind": m.get("correction_ind") == "true",
        })
    else:
        # Non-0003: include all body fields that are present, plus parsed times where available.
        event.update({k: v for k, v in m.items() if k not in ("msg_type", "received_at")})
        if actual_iso:
            event["actual_timestamp_iso"] = actual_iso

    return event


# ---------------------------------------------------------------------------
# QC reconciliation trace (used by /api/qc/{train_id})
# ---------------------------------------------------------------------------


def build_qc_trace(train_id: str, messages: list[dict], service: dict) -> dict:
    """
    Return a full reconciliation trace for a single service.
    Every message is shown with raw timestamps, parsed timestamps, computed delay,
    and whether it was included in worst/latest delay.
    """
    msgs_0003 = [m for m in messages if m.get("msg_type") == "0003"]

    # Identify the worst and latest qualifying message for inclusion flags.
    worst_delay = service.get("worst_delay_mins")
    latest_delay = service.get("latest_delay_mins")

    # Re-derive qualifying set so we can set flags.
    qualifying_received_ats: set[str] = set()
    worst_received_at: str | None = None
    latest_received_at: str | None = None

    qualifying_for_trace = []
    for m in msgs_0003:
        if m.get("delay_monitoring_point") != "true":
            continue
        dr = compute_delay(m)
        if dr["delay_mins"] is not None:
            qualifying_for_trace.append((m, dr))
            qualifying_received_ats.add(m.get("received_at", ""))

    if qualifying_for_trace:
        worst_msg = max(qualifying_for_trace, key=lambda x: x[1]["delay_mins"])
        worst_received_at = worst_msg[0].get("received_at", "")
        # Same sort key as aggregate_service: (actual_timestamp_ms, received_at)
        latest_msg = max(
            qualifying_for_trace,
            key=lambda x: (_parse_ms_timestamp(x[0].get("actual_timestamp")) or 0, x[0].get("received_at", ""))
        )
        latest_received_at = latest_msg[0].get("received_at", "")

    messages_used = []
    messages_excluded = []

    for m in messages:
        mt = m.get("msg_type", "")
        received_at = m.get("received_at", "")

        if mt != "0003":
            messages_excluded.append({
                "received_at": received_at,
                "msg_type": mt,
                "exclusion_reason": "not a 0003 message",
            })
            continue

        if m.get("delay_monitoring_point") != "true":
            messages_excluded.append({
                "received_at": received_at,
                "msg_type": mt,
                "exclusion_reason": "delay_monitoring_point=false",
            })
            continue

        dr = compute_delay(m)
        if dr["delay_mins"] is None:
            messages_excluded.append({
                "received_at": received_at,
                "msg_type": mt,
                "exclusion_reason": dr["skipped_reason"] or "delay not computable",
            })
            continue

        actual_ms = _parse_ms_timestamp(m.get("actual_timestamp"))
        gbtt_ms = _parse_ms_timestamp(m.get("gbtt_timestamp"))
        planned_ms = _parse_ms_timestamp(m.get("planned_timestamp"))

        messages_used.append({
            "received_at": received_at,
            "actual_timestamp_raw": m.get("actual_timestamp"),
            "gbtt_timestamp_raw": m.get("gbtt_timestamp"),
            "planned_timestamp_raw": m.get("planned_timestamp"),
            "actual_timestamp_parsed": _ms_to_iso(actual_ms) if actual_ms else None,
            "gbtt_timestamp_parsed": _ms_to_iso(gbtt_ms) if gbtt_ms else None,
            "planned_timestamp_parsed": _ms_to_iso(planned_ms) if planned_ms else None,
            "reference_time_used": dr["reference_time_used"],
            "computed_delay_seconds": dr["delay_seconds"],
            "computed_delay_mins": dr["delay_mins"],
            "delay_monitoring_point": True,
            "msg_type": mt,
            "included_in_worst_delay": received_at == worst_received_at,
            "included_in_latest_delay": received_at == latest_received_at,
        })

    # Plain-English derivations.
    if worst_received_at and worst_delay is not None:
        worst_deriv = (
            f"max of computed_delay_mins across qualifying messages = {worst_delay} "
            f"(from message at received_at={worst_received_at})"
        )
    else:
        worst_deriv = "no qualifying messages — worst_delay_mins is null"

    if latest_received_at and latest_delay is not None:
        latest_deriv = (
            f"computed_delay_mins from most recent qualifying message = {latest_delay} "
            f"(from message at received_at={latest_received_at})"
        )
    else:
        latest_deriv = "no qualifying messages — latest_delay_mins is null"

    trend = service.get("trend", "UNKNOWN")
    if worst_delay is not None and latest_delay is not None:
        trend_deriv = (
            f"latest ({latest_delay}) vs worst ({worst_delay}), "
            f"threshold={TREND_THRESHOLD_MINUTES} → {trend}"
        )
    else:
        trend_deriv = "trend=UNKNOWN (insufficient qualifying messages)"

    return {
        "train_id": train_id,
        "headcode": service.get("headcode"),
        "summary": {
            "worst_delay_mins": worst_delay,
            "latest_delay_mins": latest_delay,
            "trend": trend,
            "qualifying_message_count": service.get("qualifying_message_count"),
        },
        "reconciliation": {
            "all_messages_in_store": len(messages),
            "messages_used_for_delay": messages_used,
            "messages_excluded": messages_excluded,
            "worst_delay_derivation": worst_deriv,
            "latest_delay_derivation": latest_deriv,
            "trend_derivation": trend_deriv,
        },
    }
