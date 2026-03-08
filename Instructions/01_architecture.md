# Architecture and Data Model

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Backend framework | FastAPI | Async-capable, clean OpenAPI docs out of the box |
| Kafka client | `confluent-kafka` | Native librdkafka bindings, handles high-volume feeds well |
| In-memory store | Python dict with `threading.Lock` | No database, no Redis, no persistence between restarts |
| Frontend | Vanilla JS + HTML, single file | Served by FastAPI. No framework, no build step. |
| Config | `python-dotenv` | Loads `.env` at startup |

---

## Environment variables

```
KAFKA_BOOTSTRAP_SERVER=pkc-z3p1v0.europe-west2.gcp.confluent.cloud:9092
KAFKA_USERNAME=<your api key>
KAFKA_PASSWORD=<your api secret>
KAFKA_CONSUMER_GROUP=<your consumer group id>
WINDOW_MAX_MESSAGES_PER_SERVICE=10
WINDOW_MAX_AGE_MINUTES=90
```

`WINDOW_MAX_MESSAGES_PER_SERVICE` and `WINDOW_MAX_AGE_MINUTES` are the two eviction parameters (see `02_ingestion.md`). They are configurable at runtime, not hardcoded.

---

## The two-level data model

This is the foundational design decision that governs every layer of the application.

### Level 1: Message-grain store (the source of truth)

One record per Train Movement message received from the Kafka feed. This is the immutable ingestion layer. Nothing in the application modifies or summarises data at this level -- it only appends and evicts.

```
MessageStore: dict[train_id: str -> list[RawMessage]]
```

Where `RawMessage` contains the full parsed message body plus server-side metadata:
- All fields from the message `body` as-is
- `msg_type`: from the message `header`
- `received_at`: server UTC timestamp at time of ingest (not from the message itself)

### Level 2: Service-grain view (derived, query-time only)

One record per TRUST ID (`train_id`), computed on demand by reading from the Level 1 store. This layer is never written to or cached -- it is always recomputed from the message-grain data at query time.

The service-grain view answers questions like:
- What is the worst delay seen for this service in the window?
- What is the current (latest) delay?
- Is the delay trend improving or worsening?
- Is this service cancelled? Terminated?

**Nothing is pre-aggregated. The Level 1 store is the only persistent state.**

---

## Data flow

```
Kafka feed (TRAIN_MVT_ALL_TOC)
        |
        v
[Background consumer thread]
        |
    Parse message
    Append to MessageStore[train_id]
    Evict per eviction rules (see 02_ingestion.md)
        |
        v
[MessageStore] <-------- single shared in-memory structure
        |
        v
[FastAPI read threads]
        |
    Acquire read lock
    Filter messages by query params
    Compute service-grain aggregates (see 04_aggregation.md)
    Resolve dimensional lookups (see 03_reference_data.md)
    Return JSON response
        |
        v
[Frontend] polls /api/performance every 15s
           navigates to /api/service/{train_id} on row click
```

---

## Thread safety

The consumer thread writes continuously. FastAPI handles requests on separate threads. Use a single `threading.RLock` on the MessageStore. Writers acquire the lock for the duration of append + eviction. Readers acquire the lock for the duration of their copy/snapshot operation, then release before doing computation.

Do not hold the lock during aggregation computation -- take a snapshot of the relevant message list first, then release.

---

## Project structure

```
/
├── app.py                  # FastAPI app, startup, router registration
├── consumer.py             # Kafka consumer thread
├── store.py                # MessageStore class, eviction logic
├── aggregation.py          # Service-grain computation logic
├── reference.py            # Reference data loading (TOC, STANOX)
├── routers/
│   ├── performance.py      # GET /api/performance
│   ├── service.py          # GET /api/service/{train_id}
│   └── health.py           # GET /api/health
├── static/
│   └── index.html          # Single-file frontend
├── toc_ref.csv             # (user-supplied) TOC reference data
├── stanox_ref.csv          # (user-supplied) STANOX reference data
├── .env                    # (user-supplied, git-ignored) credentials
├── requirements.txt
└── README.md
```
