"""
Kafka consumer for the TRAIN_MVT_ALL_TOC feed.

Phase 1 mode (PHASE1_INGEST_SECONDS > 0):
    Consumes for exactly PHASE1_INGEST_SECONDS seconds, then stops.
    The FastAPI server stays up; the captured dataset is queryable until shutdown.

Phase 2 mode (PHASE1_INGEST_SECONDS == 0):
    Consumes continuously.  Thread runs until the process exits.

The consumer runs as a daemon background thread started at FastAPI startup.
On connection error or repeated poll timeouts it logs and retries with
exponential backoff (5s → 60s cap) without crashing the FastAPI process.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from confluent_kafka import Consumer, KafkaError, KafkaException

from store import MessageStore

logger = logging.getLogger(__name__)

TOPIC = "TRAIN_MVT_ALL_TOC"
BACKOFF_START_S = 5
BACKOFF_CAP_S = 60
POLL_TIMEOUT_S = 1.0


class KafkaConsumerThread(threading.Thread):
    def __init__(self, store: MessageStore, ingest_seconds: int = 0):
        super().__init__(daemon=True, name="kafka-consumer")
        self.store = store
        self.ingest_seconds = ingest_seconds  # 0 = continuous (Phase 2)
        self._running = threading.Event()
        self._running.set()
        self.is_consuming = False  # True while actively polling Kafka
        self.phase1_complete = False  # True after Phase 1 window closed successfully

    def stop(self):
        self._running.clear()

    def run(self):
        start_time = time.monotonic()
        backoff = BACKOFF_START_S

        while self._running.is_set():
            # Phase 1: stop after the ingest window.
            if self.ingest_seconds > 0:
                elapsed = time.monotonic() - start_time
                if elapsed >= self.ingest_seconds:
                    logger.info(
                        "Phase 1 ingest window closed after %.0fs. "
                        "Consumer stopped. %d messages captured for %d services.",
                        elapsed,
                        self.store.total_consumed,
                        self.store.cache_size,
                    )
                    self.is_consuming = False
                    self.phase1_complete = True
                    return

            consumer = self._make_consumer()
            if consumer is None:
                self.is_consuming = False
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP_S)
                continue

            try:
                consumer.subscribe([TOPIC])
                logger.info("Subscribed to %s.", TOPIC)
                backoff = BACKOFF_START_S  # reset on successful connect
                consecutive_timeouts = 0
                self.is_consuming = True

                while self._running.is_set():
                    # Phase 1 window check inside the poll loop.
                    if self.ingest_seconds > 0:
                        elapsed = time.monotonic() - start_time
                        if elapsed >= self.ingest_seconds:
                            break

                    msg = consumer.poll(timeout=POLL_TIMEOUT_S)

                    if msg is None:
                        consecutive_timeouts += 1
                        if consecutive_timeouts % 30 == 0:
                            logger.debug("No messages for %ds.", consecutive_timeouts * POLL_TIMEOUT_S)
                        continue

                    consecutive_timeouts = 0

                    if msg.error():
                        if msg.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        logger.error("Kafka error: %s", msg.error())
                        break  # reconnect

                    self._handle_message(msg)

            except KafkaException as exc:
                logger.error("KafkaException: %s — will reconnect.", exc)
                self.is_consuming = False
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP_S)
            except Exception as exc:
                logger.exception("Unexpected error in consumer thread: %s", exc)
                self.is_consuming = False
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, BACKOFF_CAP_S)
            finally:
                try:
                    consumer.close()
                except Exception:
                    pass

            # Reached here due to inner loop break or phase-1 window close.
            if self.ingest_seconds > 0:
                elapsed = time.monotonic() - start_time
                if elapsed >= self.ingest_seconds:
                    logger.info(
                        "Phase 1 ingest window closed after %.0fs. "
                        "Consumer stopped. %d messages captured for %d services.",
                        elapsed,
                        self.store.total_consumed,
                        self.store.cache_size,
                    )
                    self.is_consuming = False
                    self.phase1_complete = True
                    return
            # Otherwise: reconnect after a short pause.
            self.is_consuming = False
            self._sleep_backoff(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP_S)

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _handle_message(self, msg) -> None:
        try:
            raw = msg.value()
            if raw is None:
                return
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Could not parse message: %s", exc)
            return

        received_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Top level may be a dict (single message) or a list (batch).
        items = payload if isinstance(payload, list) else [payload]

        for item in items:
            self._ingest_item(item, received_at)

    def _ingest_item(self, item: dict, received_at: str) -> None:
        try:
            header = item.get("header", {})
            body = item.get("body", {})

            if not isinstance(body, dict):
                return

            train_id = body.get("train_id", "")
            if not train_id:
                return

            message = {
                **body,
                "msg_type": header.get("msg_type", ""),
                "received_at": received_at,
            }

            self.store.append(train_id, message)

        except Exception as exc:
            logger.warning("Error ingesting message item: %s", exc)

    # ------------------------------------------------------------------
    # Consumer factory
    # ------------------------------------------------------------------

    def _make_consumer(self) -> Consumer | None:
        bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVER", "")
        username = os.environ.get("KAFKA_USERNAME", "")
        password = os.environ.get("KAFKA_PASSWORD", "")
        group_id = os.environ.get("KAFKA_CONSUMER_GROUP", "")

        if not all([bootstrap, username, password, group_id]):
            logger.error(
                "Missing Kafka environment variables. "
                "Ensure KAFKA_BOOTSTRAP_SERVER, KAFKA_USERNAME, KAFKA_PASSWORD, "
                "KAFKA_CONSUMER_GROUP are set in .env."
            )
            return None

        try:
            consumer = Consumer({
                "bootstrap.servers": bootstrap,
                "security.protocol": "SASL_SSL",
                "sasl.mechanism": "PLAIN",
                "sasl.username": username,
                "sasl.password": password,
                "group.id": group_id,
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
            })
            return consumer
        except Exception as exc:
            logger.error("Failed to create Kafka consumer: %s", exc)
            return None

    def _sleep_backoff(self, backoff: float) -> None:
        logger.info("Retrying Kafka connection in %.0fs.", backoff)
        deadline = time.monotonic() + backoff
        while time.monotonic() < deadline and self._running.is_set():
            time.sleep(0.5)
