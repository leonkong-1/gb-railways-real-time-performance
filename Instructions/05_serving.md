# Serving Layer (API)

## General notes

All endpoints are read-only. They take a snapshot of the MessageStore, compute aggregates via `aggregation.py`, resolve references via `reference.py`, and return JSON. No endpoint writes to the store.

All timestamps in responses are ISO 8601 UTC strings.

---

## `GET /api/performance`

Returns the service-grain leaderboard.

### Query parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `toc_id` | string | none | Filter to a single operator by toc_id |
| `min_delay` | int | 3 | Minimum `worst_delay_mins` to include. Set to 0 to return all services. |
| `limit` | int | 20 | Maximum rows to return |
| `include_terminated` | bool | false | Include services where `terminated == true` |
| `include_cancelled` | bool | true | Include services where `cancelled == true` |

### Sorting

Sort by `worst_delay_mins` descending (nulls last). Secondary sort by `last_seen` descending.

### Response

```json
{
  "generated_at": "2025-03-08T14:32:00Z",
  "window_config": {
    "max_messages_per_service": 10,
    "max_age_minutes": 90
  },
  "store_summary": {
    "total_services": 142,
    "delayed_count": 18,
    "cancelled_count": 3,
    "ref_data_loaded": {
      "toc_ref": true,
      "stanox_ref": false
    }
  },
  "results": [
    {
      "train_id": "882S65MZ07",
      "headcode": "2S65",
      "toc_id": "80",
      "operator": "ScotRail",
      "last_known_location": "Haymarket",
      "worst_delay_mins": 7.0,
      "latest_delay_mins": 5.0,
      "trend": "IMPROVING",
      "variation_status": "LATE",
      "cancelled": false,
      "terminated": false,
      "message_count": 4,
      "qualifying_message_count": 3,
      "last_seen": "2025-03-08T14:31:00Z"
    }
  ]
}
```

---

## `GET /api/service/{train_id}`

Returns full message-level detail for a single service.

### Response

```json
{
  "train_id": "882S65MZ07",
  "headcode": "2S65",
  "toc_id": "80",
  "operator": "ScotRail",
  "summary": {
    "worst_delay_mins": 7.0,
    "latest_delay_mins": 5.0,
    "trend": "IMPROVING",
    "variation_status": "LATE",
    "cancelled": false,
    "terminated": false,
    "message_count": 4,
    "qualifying_message_count": 3,
    "last_seen": "2025-03-08T14:31:00Z"
  },
  "events": [
    {
      "msg_type": "0003",
      "event_type": "DEPARTURE",
      "planned_event_type": "DEPARTURE",
      "stanox": "88481",
      "location": "Haymarket",
      "platform": "5",
      "actual_timestamp": "2025-03-08T14:30:00Z",
      "gbtt_timestamp": "2025-03-08T14:29:00Z",
      "planned_timestamp": "2025-03-08T14:29:10Z",
      "computed_delay_mins": 1.0,
      "timetable_variation": "1",
      "variation_status": "LATE",
      "delay_monitoring_point": true,
      "train_terminated": false,
      "received_at": "2025-03-08T14:30:05Z"
    }
  ]
}
```

Events sorted by `actual_timestamp` ascending. For events where `actual_timestamp` is absent or invalid, fall back to `received_at` for sort order.

For non-`0003` messages included in the timeline, include `msg_type` and `received_at` at minimum. Include whatever body fields are present. The frontend uses `msg_type` to render these differently (e.g. a cancellation marker in the timeline).

---

## `GET /api/health`

```json
{
  "status": "ok",
  "consumer_running": true,
  "messages_consumed_total": 14230,
  "cache_size": 87,
  "ref_data": {
    "toc_ref_loaded": true,
    "stanox_ref_loaded": false
  },
  "last_message_received_at": "2025-03-08T14:31:58Z"
}
```

`status` is `"ok"` if the consumer thread is alive and a message has been received in the last 60 seconds. Otherwise `"degraded"`. The frontend uses this to show a stale-feed warning.

---

## `GET /`

Serves `static/index.html`.
