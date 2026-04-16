"""Thread-pool parallel map with Rich progress integration.

All functions here are I/O-bound safe — they use ``ThreadPoolExecutor``.
CPU-bound tasks (e.g. pure Python hashing) will not benefit from
threading due to the GIL, but in practice the bottleneck is always
disk/network I/O or subprocess invocations (ffprobe), so threads work well.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Sequence, TypeVar, Union

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TimeElapsedColumn

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    fn: Callable[[T], R],
    items: Sequence[T],
    *,
    workers: int = 4,
    show_progress: bool = True,
    description: str = "Procesando...",
) -> list[Union[R, BaseException]]:
    """Apply *fn* to each element of *items* using a thread pool.

    Results are returned in the **same order** as the input regardless of
    completion order.  Exceptions raised by *fn* are captured and stored at
    the corresponding index instead of propagating, so callers can decide
    per-item whether to skip or fail.

    Args:
        fn: Callable that accepts one item and returns a result.
        items: Input sequence.
        workers: Maximum thread count.  Pass ``1`` to force serial
            execution — useful for debugging and tests that use non-
            thread-safe mocks.
        show_progress: Render a Rich progress bar.
        description: Label shown in the progress bar.

    Returns:
        ``list[R | BaseException]`` — one entry per input, in input order.
    """
    if not items:
        return []

    effective_workers = max(1, min(workers, len(items)))
    results: list[Union[R, BaseException]] = [None] * len(items)  # type: ignore[list-item]

    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        disable=not show_progress,
    ) as prog:
        task = prog.add_task(description, total=len(items))

        if effective_workers == 1:
            for i, item in enumerate(items):
                try:
                    results[i] = fn(item)
                except BaseException as exc:  # noqa: BLE001
                    results[i] = exc
                    logger.debug("parallel_map error on item %d: %s", i, exc)
                prog.advance(task)
        else:
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                future_to_idx = {executor.submit(fn, item): i for i, item in enumerate(items)}
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results[idx] = future.result()
                    except BaseException as exc:  # noqa: BLE001
                        results[idx] = exc
                        logger.debug("parallel_map error on item %d: %s", idx, exc)
                    prog.advance(task)

    return results
