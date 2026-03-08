# Aggregation and Calculation Logic

## Principles

All aggregation is computed **at query time** from the Level 1 message-grain store. Nothing is pre-aggregated or cached at the service-grain level. This means every API call recomputes the service view from raw messages -- which is acceptable given the store size (tens of active services per operator, hundreds at network level).

Implement aggregation logic in `aggregation.py`. It must have no dependency on FastAPI or the Kafka consumer -- it takes a snapshot of the store as input and returns structured data.

---

## Delay calculation

### Primary method: first-principles calculation from timestamps

Delay for a single `0003` message is:

```
delay_seconds = actual_timestamp_ms - gbtt_timestamp_ms) / 1000
delay_minutes = delay_seconds / 60  (float, round to 1dp for display)
```

Where:
- `actual_timestamp`: when the train actually arrived, departed, or passed the STANOX
- `gbtt_timestamp`: the customer-facing timetable time (Great Britain Timetable) for this event. This is the authoritative planned time for delay measurement purposes.

A positive result means the train is late. A negative result means it is early.

### Handling null or zero `gbtt_timestamp`

`gbtt_timestamp` may be `"0"`, `""`, or absent on some message types or event types. This is a domain question -- do not assume handling. Before implementing, ask:

> "`gbtt_timestamp` is sometimes `"0"` or absent on `0003` messages in the feed. Can you tell me the operational meaning? For example: does this indicate a timing point that is not in the public timetable (e.g. a passing movement), and if so, should we skip delay calculation for those events entirely, or fall back to `planned_timestamp`?"

Until this is resolved, implement a fallback: if `gbtt_timestamp` is `"0"`, absent, or unparseable, skip this message for delay metric calculations (do not fall back to `planned_timestamp` silently -- that would mix two different planned time definitions without the user knowing).

### Relationship to `timetable_variation` and `variation_status`

The `timetable_variation` field and `variation_status` string are TRUST-computed values. They should be stored and surfaced in the API response as supplementary fields, but they are **not** the primary basis for any calculation in this application. Our first-principles calculation takes precedence.

---

## Service-grain aggregation

Given a list of `RawMessage` objects for a single `train_id`, compute the following.

### Filtering before calculation

Only use `0003` messages for all delay metric calculations. Other message types (activations, cancellations) are used for status flags only.

For delay metrics specifically, further filter to messages where `delay_monitoring_point == "true"`. Other movement messages can be used for the stop-by-stop timeline display but should not contribute to worst/latest delay figures.

### Computed fields

**`headcode`**: characters at index 2-5 (0-indexed) of `train_id`. E.g. `"882S65MZ07"` → `"2S65"`.

**`toc_id`**: from the most recent `0003` message body.

**`operator`**: `toc_id` resolved via reference data.

**`last_known_location`**: `loc_stanox` from the most recent `0003` message, resolved via reference data.

**`worst_delay_mins`**: maximum computed delay (first-principles) across all qualifying messages (0003, delay_monitoring_point = true, valid gbtt_timestamp). If no qualifying messages exist, this is `null`.

**`latest_delay_mins`**: computed delay from the single most recent qualifying message. If no qualifying messages exist, this is `null`.

**`trend`**: derived from `worst_delay_mins` and `latest_delay_mins`:
- If either is `null`: `"UNKNOWN"`
- If `latest_delay_mins` < `worst_delay_mins` - 2: `"IMPROVING"`
- If `latest_delay_mins` > `worst_delay_mins` + 2: `"WORSENING"`
- Otherwise: `"STABLE"`

The threshold of 2 minutes is a default. Document it as a configurable constant, not a magic number.

**`variation_status`**: `variation_status` field from the most recent `0003` message. Advisory display value.

**`terminated`**: `true` if any message has `train_terminated == "true"`.

**`cancelled`**: `true` if a `0002` message exists for this `train_id` and no subsequent `0005` (reinstatement) message exists. Determine "subsequent" by comparing `received_at` timestamps.

**`message_count`**: total number of messages of any type for this `train_id` in the store.

**`qualifying_message_count`**: number of `0003` messages with `delay_monitoring_point == "true"` and a valid `gbtt_timestamp`. This is the count that actually contributes to delay metrics -- useful for debugging and confidence assessment.

**`last_seen`**: `received_at` from the most recent message of any type.

---

## Stop-by-stop timeline (for service drilldown)

For the `/api/service/{train_id}` endpoint, return all messages for the service sorted by `actual_timestamp` ascending (or `received_at` if `actual_timestamp` is absent/invalid).

For each `0003` message in the timeline, include the first-principles computed delay alongside the raw TRUST fields. For non-0003 messages, include them with a clear `msg_type` label so the user can see activations and cancellations in sequence.

---

## Domain questions to surface before finalising

In addition to the `gbtt_timestamp` zero-value question above, surface the following before completing this layer:

1. > "Should services with `variation_status == 'OFF ROUTE'` appear in the delay leaderboard alongside `LATE` services, or be shown in a separate category? Their `timetable_variation` may be meaningless if they are not following the scheduled path."

2. > "For the `cancelled` flag: if a service is cancelled (`0002`) and then reinstated (`0005`) and then cancelled again (`0002`), the current logic would mark it as not-cancelled because we look for any `0005` after any `0002`. Is that the right behaviour, or should we track the most recent event type?"

3. > "The trend threshold of 2 minutes (IMPROVING/WORSENING boundary) is arbitrary. Is there an operational norm for what constitutes a meaningful change in delay for performance monitoring purposes?"
