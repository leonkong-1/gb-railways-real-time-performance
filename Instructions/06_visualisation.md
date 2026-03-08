# Visualisation Layer

## Principles

The frontend is a **single self-contained HTML file** (`static/index.html`) served by FastAPI. No external CSS frameworks, no build step, no CDN dependencies. Vanilla JS only. It should render correctly with no internet connection.

The aesthetic target is: functional, readable in a control room context (dark background acceptable), and fast to scan. Not beautiful.

---

## Views

The frontend has two views rendered within the same page. Navigation between them does not require a page reload.

### View 1: Leaderboard (default)

Displayed on load and after clicking "Back" from the drilldown.

**Header bar**
- Title: "Train Performance Monitor"
- Rolling window label (e.g. "Last 90 min / max 10 msgs per service")
- Last refreshed timestamp
- Feed status indicator: green dot if healthy, amber if degraded (based on `/api/health` response)

**Summary stat boxes** (three, inline)
- Total services tracked
- Delayed (worst_delay ≥ selected min_delay threshold)
- Cancelled

**Filter bar**
- TOC dropdown: "All operators" plus one entry per distinct `toc_id` seen in current results, labelled with `operator` name (or raw code if reference data not loaded)
- Min delay selector: 0 / 3 / 5 / 10 minutes
- Filters apply immediately on change without requiring a manual refresh

**Results table**

Columns:
| Column | Source field | Notes |
|---|---|---|
| Headcode | `headcode` | |
| Operator | `operator` | |
| Last Location | `last_known_location` | |
| Worst Delay | `worst_delay_mins` | Display as e.g. "+7 min" |
| Latest Delay | `latest_delay_mins` | Display as e.g. "+5 min" |
| Trend | `trend` | `▼ Improving`, `▲ Worsening`, `→ Stable`, `? Unknown` |
| Status | `variation_status` | Advisory label |
| Msgs | `qualifying_message_count` | Small, muted -- for confidence assessment |
| Last Seen | `last_seen` | Relative time, e.g. "2 min ago" |

Row colour coding by `worst_delay_mins`:
- < 3 min: no highlight (or subtle green)
- 3-9 min: amber
- ≥ 10 min: red
- Cancelled: distinct badge or strikethrough, not delay-coloured

Each row is **clickable**. Clicking navigates to View 2 (drilldown) for that `train_id`.

**Auto-refresh**: poll `/api/performance` (with current filter params) every 15 seconds. Refresh the table in place. Do not scroll the user back to the top on refresh if they have scrolled down.

---

### View 2: Service drilldown

Displayed when a row is clicked in the leaderboard.

**Navigation**
- "← Back" button returns to the leaderboard (preserving filter state)
- Header shows: Headcode, Operator, current Status, Worst Delay / Latest Delay, Trend

**Timeline table**

One row per event in `/api/service/{train_id}` response, sorted chronologically.

Columns:
| Column | Source field | Notes |
|---|---|---|
| Time (actual) | `actual_timestamp` | Formatted as HH:MM:SS |
| Location | `location` | Resolved STANOX name |
| Event | `event_type` | DEPARTURE / ARRIVAL / PASS |
| Platform | `platform` | |
| Planned (GBTT) | `gbtt_timestamp` | Formatted as HH:MM:SS, shown as "--" if absent |
| Delay | `computed_delay_mins` | "+X min" or "-X min", blank if not computable |
| Timing Point | `delay_monitoring_point` | Tick mark or blank |
| Msg Type | `msg_type` | Small badge -- useful for seeing 0001/0002/0005 events in sequence |

Row colour coding: same delay threshold scheme as leaderboard, applied to `computed_delay_mins`.

Non-`0003` messages (activations, cancellations, reinstatements) should be displayed as distinct rows with a different background or label, so the user can see them in the sequence without confusing them with movement events.

**No auto-refresh on drilldown.** Data is fetched once when the view is opened. A manual "Refresh" button is acceptable if straightforward to implement.

---

## Feed stale warning

If `/api/health` returns `status: "degraded"` or the last message received is more than 60 seconds ago, display a visible banner: "Feed may be stalled -- data may not be current." Do not hide or minimise this.

---

## Domain question to surface

> "Should the drilldown show only the messages within the current store window, or ideally all messages received since the app started (which would require a different retention model)? For v1 the store-window view is assumed, but worth confirming this is acceptable for the control room use case."
