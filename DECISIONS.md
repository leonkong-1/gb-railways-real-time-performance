# Design Decisions — GB Train Performance Dashboard

## Architecture

**No database, no Redis**: pure in-memory Python dict with threading.RLock.
Rationale: restarts are acceptable; persistence adds deployment complexity for no
operational benefit (TRUST feed replays from "latest" on reconnect anyway).

**Two-level data model**: Level 1 = message-grain store (immutable append+evict).
Level 2 = service-grain (always computed at query time, never cached).
Rationale: avoids stale pre-aggregations; store size is bounded (hundreds of services).

**OFF ROUTE excluded from leaderboard**: services with offroute_ind=true get a
separate count in store_summary.off_route_count. Their timetable_variation is "0"
(wiki-confirmed) and delay calculation has no valid reference. Not shown in delay table.

**Trend threshold = 2 minutes, inclusive**: `latest <= worst - 2 → IMPROVING`.
Boundary is inclusive to match the spec example (worst=7, latest=5 → IMPROVING).

## Domain

**Max age = 240 minutes (not spec's 90)**: Highland route services can have inter-TRUST
gaps > 90 min. 240 min confirmed as safe upper bound with user.

**gbtt_timestamp fallback to planned_timestamp**: wiki confirms planned_timestamp is
always present; gbtt_timestamp absent for freight, non-public calls, no passenger entry.
QC trace shows which reference was used ("gbtt" or "planned").

**Cancelled = most-recent of 0002/0005 by received_at**: handles cancel→reinstate→cancel
cycles correctly. Original spec logic (any 0002 + no subsequent 0005) was wrong for cycles.

**Eviction: non-terminated services keep all messages** (updated after Phase 1):
Original Rule 1 (latest-N truncation) applied to all services. This produced incomplete
event timelines in drilldown for long-distance services — users saw only last 10 stops.
Updated: Rule 1 only applies to terminated services. Non-terminated services are
evicted only by Rule 2 (max-age). Memory impact is negligible at observed scale.

## Frontend

**TRUST ID always shown alongside headcode**: headcode (4-char) is not unique within
a day — it's recycled within the same TOC. TRUST ID (10-char) is the unique identifier.
Both shown; TRUST ID is click-to-copy to support QC URL lookups.

**TOC dropdown from /api/tocs not from filtered results**: original implementation
populated the TOC list from the current result set, so TOCs with no delayed services
at the current min_delay threshold were invisible. Fixed: separate /api/tocs call
returns all TOCs currently in the store, regardless of delay status.

**Limit = 500 for network view, unlimited for single-TOC view**: network leaderboard
capped at 500 (practical display limit). When a TOC is selected, limit removed so
all services for that TOC are shown (even low/zero delay ones if min_delay=0).

## Hosting (future)
GitHub Pages not viable (no backend). Options discussed: Fly.io, Railway, Render
(all support Python + persistent background threads). See README for deployment notes.
