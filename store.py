"""
MessageStore: Level 1 in-memory message-grain store.

Keyed by train_id -> list[dict] (raw messages, ordered oldest-first).
Eviction applies on every append AND via a periodic background sweep:

  Rule 1 – keep only the latest MAX_MESSAGES_PER_SERVICE messages per train_id.
            ONLY applied to terminated services (train_terminated == "true" seen).
            Non-terminated services keep ALL messages so their full event timeline
            is always available in the drilldown view.

  Rule 2 – if the most-recent message's received_at is older than the age threshold,
            drop the entire train_id entry. Age threshold varies by service status:
              • Cancelled or terminated: max_age_minutes (default 240 / 4 hours)
              • Neither (zombie — no cancellation/termination ever received):
                max_age_zombie_minutes (default 480 / 8 hours)

  Background sweep – a daemon thread runs _eviction_sweep() every sweep_interval_seconds
            (default 600 / 10 minutes). This catches services that have gone silent and
            will never receive another message, which Rule 2 on append alone cannot evict.

Both rules together avoid the failure modes of either rule alone:
  Rule 1 alone keeps stale services indefinitely if they hit exactly N messages.
  Rule 2 alone drops long-distance services that are active but between reporting points.

NOTE: max_age_minutes defaults to 240 (4 hours) rather than 90 to cover services
on sparse Highland routes where inter-TRUST-report gaps can exceed 90 minutes.
"""

import threading
from datetime import datetime, timezone


class MessageStore:
    def __init__(
        self,
        max_messages_per_service: int = 10,
        max_age_minutes: int = 240,
        max_age_zombie_minutes: int = 480,
    ):
        self._store: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._total_consumed: int = 0
        self._last_message_received_at: str | None = None
        self.max_messages_per_service = max_messages_per_service
        self.max_age_minutes = max_age_minutes
        self.max_age_zombie_minutes = max_age_zombie_minutes
        self._sweep_stop = threading.Event()
        self._sweep_thread: threading.Thread | None = None

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

            # Rule 2: if the most-recent message is older than the age threshold, evict.
            most_recent_received_at = self._store[train_id][-1].get("received_at", "")
            if most_recent_received_at:
                threshold = self._age_threshold(self._store[train_id])
                if self._age_minutes(most_recent_received_at) > threshold:
                    del self._store[train_id]

    # ------------------------------------------------------------------
    # Background eviction sweep (catches services that go permanently silent)
    # ------------------------------------------------------------------

    def start_sweep(self, interval_seconds: int = 600) -> None:
        """Start a daemon thread that periodically evicts stale train_ids."""
        self._sweep_stop.clear()
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop,
            args=(interval_seconds,),
            daemon=True,
            name="eviction-sweep",
        )
        self._sweep_thread.start()

    def stop_sweep(self) -> None:
        """Signal the sweep thread to stop (used on shutdown)."""
        self._sweep_stop.set()

    def _sweep_loop(self, interval_seconds: int) -> None:
        while not self._sweep_stop.wait(timeout=interval_seconds):
            self._eviction_sweep()

    def _eviction_sweep(self) -> None:
        """Walk the entire store and evict train_ids whose last message is too old."""
        with self._lock:
            to_delete = []
            for train_id, msgs in self._store.items():
                if not msgs:
                    to_delete.append(train_id)
                    continue
                most_recent = msgs[-1].get("received_at", "")
                if most_recent:
                    threshold = self._age_threshold(msgs)
                    if self._age_minutes(most_recent) > threshold:
                        to_delete.append(train_id)
            for train_id in to_delete:
                del self._store[train_id]

    # ------------------------------------------------------------------
    # Eviction helpers
    # ------------------------------------------------------------------

    def _age_threshold(self, msgs: list[dict]) -> int:
        """
        Return the max-age threshold in minutes for a list of messages.
        Cancelled or terminated services: max_age_minutes (default 240).
        Zombie services (no cancellation/termination): max_age_zombie_minutes (default 480).
        """
        if self._is_terminated(msgs) or self._is_cancelled(msgs):
            return self.max_age_minutes
        return self.max_age_zombie_minutes

    @staticmethod
    def _is_terminated(msgs: list[dict]) -> bool:
        return any(m.get("train_terminated") == "true" for m in msgs)

    @staticmethod
    def _is_cancelled(msgs: list[dict]) -> bool:
        """True if the most recent 0002/0005 message is a 0002 (cancellation)."""
        latest_ts = ""
        latest_is_cancelled = False
        for m in msgs:
            mt = m.get("msg_type")
            if mt not in ("0002", "0005"):
                continue
            ts = m.get("received_at", "")
            if ts >= latest_ts:
                latest_ts = ts
                latest_is_cancelled = (mt == "0002")
        return latest_is_cancelled

    def _age_minutes(self, received_at_iso: str) -> float:
        """Return how many minutes ago the given ISO timestamp was. Returns 0 on error."""
        try:
            then = datetime.fromisoformat(received_at_iso.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            return (now - then).total_seconds() / 60
        except Exception:
            return 0

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
