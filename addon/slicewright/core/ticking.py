# SPDX-License-Identifier: GPL-3.0-or-later
"""Cooperative chunked work under a per-tick time budget (03 sections 3.4, 7.8 and 8.6).

The add-on has no threads. Anything that would block the UI (building the profile index,
uploading preview chunks, writing the slice cache, network sends) is written as a
*generator*: each ``yield`` ends one small step of work. A :class:`TickRunner` advances its
tasks round-robin until a time budget is spent, then returns; Blender calls it again from a
``bpy.app.timers`` callback. This module has no ``bpy`` dependency, so the same code runs
blocking in unit tests (:meth:`TickRunner.run_until_idle`) and from a timer in Blender.

What a task's generator may yield (all optional):

* ``None``: nothing to report.
* a float in ``[0, 1]``: progress as a fraction.
* ``(fraction, message)``: progress and a status message.

Its ``return`` value is passed to ``on_done``. If it raises, ``on_error`` is called and the
task fails; nothing ever propagates out of :meth:`TickRunner.tick`, because an exception in a
Blender timer callback would silently unregister the timer.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

log = logging.getLogger("slicewright.ticking")

PENDING, DONE, FAILED, CANCELLED = "pending", "done", "failed", "cancelled"
DEFAULT_BUDGET_S = 0.025        # ~one 60 Hz frame is 16 ms; 25 ms keeps the UI responsive
DEFAULT_INTERVAL_S = 0.0        # ask to be called again as soon as the UI has had its turn


@dataclass
class Task:
    name: str
    _gen: Iterator
    on_done: Callable[[Any], None] | None = None
    on_error: Callable[[BaseException], None] | None = None
    on_cancel: Callable[[], None] | None = None
    state: str = PENDING
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: BaseException | None = None
    steps: int = 0
    _cancel_requested: bool = field(default=False, repr=False)

    @property
    def finished(self) -> bool:
        return self.state != PENDING


class TickRunner:
    """Round-robin scheduler for generator tasks with a per-tick time budget."""

    def __init__(self, budget_s: float = DEFAULT_BUDGET_S, interval_s: float = DEFAULT_INTERVAL_S,
                 clock: Callable[[], float] = time.perf_counter,
                 wake: Callable[[], None] | None = None) -> None:
        if budget_s <= 0:
            raise ValueError("budget_s must be positive")
        self.budget_s = budget_s
        self.interval_s = interval_s
        self.wake = wake                    # called when work arrives while idle (arms the timer)
        self._clock = clock
        self._tasks: list[Task] = []        # pending, in round-robin order
        self._by_name: dict[str, Task] = {}
        self._running: Task | None = None
        self.ticks = 0
        self.steps = 0

    # -- submitting and cancelling -----------------------------------------------------------

    def submit(self, name: str, work: Iterable | Callable[[], Iterator], *,
               on_done: Callable[[Any], None] | None = None,
               on_error: Callable[[BaseException], None] | None = None,
               on_cancel: Callable[[], None] | None = None, replace: bool = False) -> Task:
        """Queue ``work`` (a generator, or a function returning one). Names are unique among
        pending tasks; with ``replace=True`` an existing task of that name is cancelled first."""
        if name in self._by_name:
            if not replace:
                raise ValueError(f"a task named {name!r} is already pending")
            self.cancel(name)
        gen = work() if callable(work) else iter(work)
        task = Task(name, gen, on_done, on_error, on_cancel)
        was_idle = not self._tasks
        self._tasks.append(task)
        self._by_name[name] = task
        if was_idle and self.wake is not None:
            self.wake()
        return task

    def cancel(self, which: str | Task) -> bool:
        """Cancel a pending task: its generator is closed (so ``finally`` blocks run) and
        ``on_cancel`` is called. Safe to call from inside any callback. Returns False if there
        was nothing to cancel."""
        task = self._by_name.get(which) if isinstance(which, str) else which
        if task is None or task.finished:
            return False
        if task is self._running:           # cancelling itself or from its own callback
            task._cancel_requested = True
            return True
        self._finish(task, CANCELLED)
        return True

    def cancel_all(self) -> None:
        for task in list(self._tasks):
            self.cancel(task)

    # -- queries -----------------------------------------------------------------------------

    @property
    def idle(self) -> bool:
        return not self._tasks

    def pending(self) -> list[Task]:
        return list(self._tasks)

    def get(self, name: str) -> Task | None:
        return self._by_name.get(name)

    # -- running -----------------------------------------------------------------------------

    def tick(self) -> float | None:
        """Advance tasks for about ``budget_s`` seconds.

        At least one step always runs, so progress is guaranteed even with a tiny budget; a single
        step is never interrupted, so keep steps short. Returns the delay until the next call, or
        ``None`` when there is nothing left to do (a Blender timer returning ``None`` stops).
        """
        self.ticks += 1
        deadline = self._clock() + self.budget_s
        while self._tasks:
            task = self._tasks.pop(0)
            self._step(task)
            if not task.finished:
                self._tasks.append(task)
            if self._clock() >= deadline:
                break
        return None if not self._tasks else self.interval_s

    def run_until_idle(self, max_ticks: int = 1_000_000) -> int:
        """Blocking drive for tests and scripts. Returns the number of ticks used."""
        used = 0
        while self._tasks:
            if used >= max_ticks:
                raise RuntimeError(f"tasks still pending after {max_ticks} ticks")
            self.tick()
            used += 1
        return used

    def _step(self, task: Task) -> None:
        self._running = task
        try:
            if task._cancel_requested:
                self._finish(task, CANCELLED)
                return
            try:
                value = next(task._gen)
                task.steps += 1
                self.steps += 1
                self._note_progress(task, value)
            except StopIteration as stop:
                task.result = stop.value
                self._finish(task, DONE)
            except Exception as exc:  # noqa: BLE001 - tasks must never break the timer
                task.error = exc
                log.exception("task %r failed", task.name)
                self._finish(task, FAILED)
            else:
                if task._cancel_requested:
                    self._finish(task, CANCELLED)
        finally:
            self._running = None

    @staticmethod
    def _note_progress(task: Task, value: Any) -> None:
        if isinstance(value, tuple) and len(value) == 2:
            fraction, message = value
            task.message = str(message)
        else:
            fraction = value
        if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
            task.progress = min(1.0, max(0.0, float(fraction)))

    def _finish(self, task: Task, state: str) -> None:
        """Remove ``task`` from the queue, close its generator and run the matching callback."""
        task.state = state
        if task in self._tasks:
            self._tasks.remove(task)
        if self._by_name.get(task.name) is task:
            del self._by_name[task.name]
        if state in (CANCELLED, FAILED):
            try:
                task._gen.close()
            except Exception:  # noqa: BLE001
                log.exception("closing task %r failed", task.name)
        if state == DONE:
            task.progress = 1.0
        callback, arg = {
            DONE: (task.on_done, task.result), FAILED: (task.on_error, task.error),
            CANCELLED: (task.on_cancel, None)}[state]
        if callback is not None:
            try:
                callback(arg) if state != CANCELLED else callback()
            except Exception:  # noqa: BLE001 - a bad callback must not kill the timer
                log.exception("callback for task %r (%s) failed", task.name, state)


def chunked(items: Sequence, chunk_size: int, fn: Callable[[Any], Any]) -> Iterator[float]:
    """Generator helper: apply ``fn`` to ``items`` ``chunk_size`` at a time, one yield per chunk.

    Yields the progress fraction after each chunk and returns the list of results, e.g.
    ``task = runner.submit("index", chunked(files, 300, parse), on_done=store)``.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    results = []
    total = len(items)
    for start in range(0, total, chunk_size):
        results.extend(fn(item) for item in items[start:start + chunk_size])
        yield min(1.0, (start + chunk_size) / total)
    return results
