from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import TypeVar


T = TypeVar("T")


class SlotDeadlineExceeded(TimeoutError):
    pass


class NoProgressError(TimeoutError):
    pass


class PipelineWatchdog:
    def __init__(self, slot_deadline_seconds: float, no_progress_seconds: float):
        self.started_at = time.monotonic()
        self.deadline = self.started_at + slot_deadline_seconds
        self.no_progress_seconds = no_progress_seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def ensure_alive(self, last_progress_monotonic: float) -> None:
        now = time.monotonic()
        if now >= self.deadline:
            raise SlotDeadlineExceeded("slot deadline exceeded")
        if now - last_progress_monotonic > self.no_progress_seconds:
            raise NoProgressError("no real retrieval progress within watchdog window")

    async def run(self, awaitable: Awaitable[T], operation_timeout_seconds: float) -> T:
        timeout = min(self.remaining(), operation_timeout_seconds, self.no_progress_seconds)
        if timeout <= 0:
            raise SlotDeadlineExceeded("slot deadline exceeded before operation started")
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise NoProgressError(f"operation made no progress for {timeout:.1f}s") from exc
