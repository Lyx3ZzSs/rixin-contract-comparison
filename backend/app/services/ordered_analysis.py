"""Bounded, ordered CPU analysis; input acquisition stays on the caller thread."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Iterator, TypeVar


T = TypeVar("T")
R = TypeVar("R")


def ordered_analysis(function: Callable[[T], R], items: Iterable[T], max_inflight: int = 1) -> Iterator[R]:
    """Workers must receive independent arrays/data, never live PDF handles.

    Consume at most max_inflight inputs ahead, propagate errors and preserve
    input order. Closing the iterator also joins the bounded worker pool.
    """
    if max_inflight <= 1:
        yield from map(function, items)
        return
    source = iter(items)
    with ThreadPoolExecutor(max_workers=max_inflight, thread_name_prefix="image-analysis") as executor:
        pending = deque()
        try:
            for _ in range(max_inflight):
                try:
                    item = next(source)
                except StopIteration:
                    break
                pending.append(executor.submit(function, item))
            while pending:
                result = pending.popleft().result()
                yield result
                try:
                    item = next(source)
                except StopIteration:
                    continue
                pending.append(executor.submit(function, item))
        finally:
            for future in pending:
                future.cancel()
