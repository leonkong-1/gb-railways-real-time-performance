# Ingestion Layer

## Kafka connection

Connect to the feed using `confluent-kafka`'s `Consumer` class with the following config:

```python
{
    'bootstrap.servers': KAFKA_BOOTSTRAP_SERVER,
    'security.protocol': 'SASL_SSL',
    'sasl.mechanism': 'PLAIN',
    'sasl.username': KAFKA_USERNAME,
    'sasl.password': KAFKA_PASSWORD,
    'group.id': KAFKA_CONSUMER_GROUP,
    'auto.offset.reset': 'latest',  # on first connect, start from now not from beginning
    'enable.auto.commit': True,
}
```

Subscribe to topic: `TRAIN_MVT_ALL_TOC`

Run the consumer in a **daemon background thread** started at FastAPI startup. The FastAPI process must not block waiting for Kafka.

If the consumer raises a connection error or poll timeout, log the error and attempt reconnection with exponential backoff (start at 5s, cap at 60s). Do not crash the FastAPI process.

---

## Message parsing

Each Kafka message value is a JSON string. Parse it into a dict. The structure is:

```json
{
  "header": {
    "msg_type": "0003",
    "msg_queue_timestamp": "...",
    "source_system_id": "TRUST",
    "original_data_source": "SMART"
  },
  "body": { ... }
}
```

Some feeds wrap multiple messages in a list at the top level. Handle both `dict` and `list` at the top level gracefully -- if a list, iterate and process each element.

Extract:
- `msg_type` from `header`
- All fields from `body` as-is (preserve as strings; do not cast types during ingest)
- Add `received_at` as a server-side UTC ISO timestamp string at time of parse

Ingest **all message types** without filtering. Type-based filtering happens at query time, not here.

---

## MessageStore design

Implement as a class in `store.py`:

```python
class MessageStore:
    def __init__(self, max_messages_per_service: int, max_age_minutes: int):
        self._store: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._total_consumed = 0

    def append(self, train_id: str, message: dict) -> None:
        ...

    def get_messages(self, train_id: str) -> list[dict]:
        ...

    def get_all_train_ids(self) -> list[str]:
        ...

    def snapshot(self) -> dict[str, list[dict]]:
        # Returns a shallow copy of the store for safe read-side use
        ...

    @property
    def total_consumed(self) -> int:
        ...

    @property
    def cache_size(self) -> int:
        # Number of distinct train_ids currently in store
        ...
```

---

## Eviction logic

**This is not purely time-based.** A time-only window would evict long-distance services that have infrequent station calls, removing all trace of a massively delayed service that simply has not passed a reporting point recently.

Apply a **conjunction of two rules** on every `append()` call, after adding the new message:

### Rule 1: Latest-N per TRUST ID

After appending, truncate the message list for this `train_id` to the most recent `MAX_MESSAGES_PER_SERVICE` messages (default: 10), sorted by `received_at` descending. This ensures every active service retains at least its last known state regardless of how long ago the messages were received.

### Rule 2: Maximum age per TRUST ID

After applying Rule 1, check the timestamp of the **most recent message** for this `train_id`. If that most recent message is older than `MAX_AGE_MINUTES` (default: 90 minutes), drop the entire `train_id` entry from the store. This removes genuinely dead or terminated services that have not produced any messages for an extended period.

**The conjunction matters**: Rule 1 alone would keep stale services forever if they had exactly N messages. Rule 2 alone would drop long-distance services mid-journey. Together they handle both cases.

### Timestamp to use for age comparison

Use `received_at` (server-side ingest timestamp) rather than `actual_timestamp` from the message body. This avoids issues with message body timestamps that are null, zero, or malformed.

### Domain question to ask before implementing

> "The default of 90 minutes for max age is an assumption. Is there a known maximum gap between station calls for any GB service that would require a longer window? For example, are there services with cross-country legs where the gap between TRUST reporting points regularly exceeds 90 minutes?"

---

## Sample message reference (`0003`)

```json
{
  "header": {
    "msg_type": "0003",
    "msg_queue_timestamp": "1772916225000",
    "source_system_id": "TRUST",
    "original_data_source": "SMART"
  },
  "body": {
    "train_id": "882S65MZ07",
    "actual_timestamp": "1772916180000",
    "loc_stanox": "88481",
    "gbtt_timestamp": "1772916120000",
    "planned_timestamp": "1772916150000",
    "planned_event_type": "DEPARTURE",
    "event_type": "DEPARTURE",
    "event_source": "AUTOMATIC",
    "correction_ind": "false",
    "offroute_ind": "false",
    "direction_ind": "DOWN",
    "toc_id": "80",
    "timetable_variation": "1",
    "variation_status": "LATE",
    "next_report_stanox": "88468",
    "next_report_run_time": "3",
    "train_terminated": "false",
    "delay_monitoring_point": "true",
    "reporting_stanox": "88481",
    "auto_expected": "true"
  }
}
```

Key fields and their types (all arrive as strings from the feed):
- `train_id`: TRUST ID string. Headcode is characters at index 2-5 (0-indexed).
- `actual_timestamp`: Unix milliseconds string. The time the event actually occurred.
- `gbtt_timestamp`: Unix milliseconds string. The customer-facing timetable time for this event. **Primary basis for delay calculation.** May be `"0"` or absent for some event types -- see `04_aggregation.md` for handling.
- `planned_timestamp`: Unix milliseconds string. The working timetable time (WTT). Secondary reference.
- `timetable_variation`: integer string, minutes. TRUST-computed delay value. Treat as advisory only -- do not use as the primary delay figure.
- `variation_status`: TRUST-computed status string (`LATE`, `EARLY`, `ON TIME`, `OFF ROUTE`). Treat as advisory.
- `delay_monitoring_point`: `"true"` or `"false"`. Indicates whether this event is a meaningful timing point for performance purposes.
- `train_terminated`: `"true"` or `"false"`.
