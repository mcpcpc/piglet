"""A cooperative cancellation signal shared by providers, tools and the agent loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class AbortedError(Exception):
    """Raised when an operation stops because its abort signal fired."""


class AbortSignal:
    """An abort flag that awaiting code can race against (like a DOM ``AbortSignal``)."""

    def __init__(self) -> None:
        """Create an un-aborted signal."""
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []
        self.reason: str | None = None

    @property
    def aborted(self) -> bool:
        """Whether :meth:`abort` has been called."""
        return self._event.is_set()

    def abort(self, reason: str = "aborted") -> None:
        """Fire the signal and run registered callbacks once."""
        if self._event.is_set():
            return
        self.reason = reason
        self._event.set()
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    def on_abort(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` when the signal fires (immediately if it already has)."""
        if self.aborted:
            callback()
        else:
            self._callbacks.append(callback)

    async def wait(self) -> None:
        """Wait until the signal fires."""
        await self._event.wait()

    def raise_if_aborted(self) -> None:
        """Raise :class:`AbortedError` if the signal has fired."""
        if self.aborted:
            raise AbortedError(self.reason or "aborted")

    async def race(self, awaitable: Awaitable[T]) -> T:
        """Await ``awaitable`` but raise :class:`AbortedError` as soon as the signal fires.

        Args:
            awaitable: The work to await.

        Returns:
            The awaitable's result.

        Raises:
            AbortedError: If the signal fires first; the work is cancelled.
        """
        self.raise_if_aborted()
        work = asyncio.ensure_future(awaitable)
        waiter = asyncio.ensure_future(self._event.wait())
        try:
            done, _ = await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if work in done:
            return work.result()
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        raise AbortedError(self.reason or "aborted")
