"""
MessageStore: Level 1 in-memory message-grain store.

Keyed by train_id -> list[dict] (raw messages, ordered oldest-first).
Eviction applies on every append:
  Rule 1 – keep only the latest MAX_MESSAGES_PER_SERVICE messages per train_id.
            ONLY applied to terminated services (train_terminated == "true" seen).
            Non-terminated services keep ALL messages so their full event timeline
            is always available in the drilldown view.
  Rule 2 – if the most-recent message's received_at is older than MAX_AGE_MINUTES,
            drop the entire train_id entry.

Both rules together avoid the failure modes of either rule alone:
  Rule 1 alone keeps stale services indefinitely if they hit exactly N messages.
  Rule 2 alone drops long-distance services that are active but between reporting points.

NOTE: MAX_AGE_MINUTES defaults to 240 (4 hours) rather than 90 to cover services
on sparse Highland routes where inter-TRUST-report gaps can exceed 90 minutes.
"""

import threading
from datetime import datetime, timezone


class MessageStore:
    def __init__(self, max_messages_per_service: int = 10, max_age_minutes: int = 240):
        self._store: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._total_consumed: int = 0
        self._last_message_received_at: str | None = None
        self.max_messages_per_service = max_messages_per_service
        self.max_age_minutes = max_age_minutes

    # ------------------------------------------------------------------
    # Write path (called from consumer thread)
    # ------------------------------------------------------------------

    def append(self, train_id: str, message: dict) -> None:
        """Append a message for train_id and apply eviction rules."""
        with self._lock:
            self._total_consumed += 1
            self._last_message_received_at = message.get("received_at")

            if train_id not in self._store:
                self._store[train_id] = []

            self._store[train_id].append(message)

            # Rule 1: truncate to latest N messages — terminated services only.
            # Non-terminated services keep ALL messages; users expect to see the full
            # event timeline for an active service in the drilldown view.
            msgs = self._store[train_id]
            terminated = any(m.get("train_terminated") == "true" for m in msgs)
            if terminated and len(msgs) > self.max_messages_per_service:
                msgs.sort(key=lambda m: m.get("received_at", ""), reverse=True)
                msgs = msgs[: self.max_messages_per_service]
                msgs.sort(key=lambda m: m.get("received_at", ""))
                self._store[train_id] = msgs

            # Rule 2: if the most-recent message is older than MAX_AGE_MINUTES, evict.
            most_recent_received_at = self._store[train_id][-1].get("received_at", "")
            if most_recent_received_at and self._is_older_than_max_age(most_recent_received_at):
                del self._store[train_id]

    def _is_older_than_max_age(self, received_at_iso: str) -> bool:
        """Return True if the given ISO timestamp is older than max_age_minutes."""
        try:
            then = datetime.fromisoformat(received_at_iso.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            age_minutes = (now - then).total_seconds() / 60
            return age_minutes > self.max_age_minutes
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Read path (called from FastAPI threads)
    # ------------------------------------------------------------------

    def get_messages(self, train_id: str) -> list[dict]:
        """Return a copy of the message list for a single train_id."""
        with self._lock:
            return list(self._store.get(train_id, []))

    def get_all_train_ids(self) -> list[str]:
        """Return a copy of all train_ids currently in the store."""
        with self._lock:
            return list(self._store.keys())

    def snapshot(self) -> dict[str, list[dict]]:
        """
        Return a shallow copy of the store for safe read-side use.
        The outer dict is a new object; each message list is a new list
        (messages themselves are not deep-copied — treat as read-only).
        Release the lock before doing any computation.
        """
        with self._lock:
            return {tid: list(msgs) for tid, msgs in self._store.items()}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_consumed(self) -> int:
        with self._lock:
            return self._total_consumed

    @property
    def cache_size(self) -> int:
        """Number of distinct train_ids currently in the store."""
        with self._lock:
            return len(self._store)

    @property
    def last_message_received_at(self) -> str | None:
        with self._lock:
            return self._last_message_received_at
