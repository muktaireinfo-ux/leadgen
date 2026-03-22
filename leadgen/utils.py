"""Shared utilities: retry logic with exponential backoff."""

import time
import random
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_with_backoff(fn: Callable[[], T], retries: int = 3, base_delay: float = 1.0) -> T:
    """Call fn(), retrying up to `retries` times with exponential backoff on exception.

    Delays: 1s, 2s, 4s (with ±25% jitter).
    Raises the last exception if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries:
                delay = base_delay * (2 ** attempt) * random.uniform(0.75, 1.25)
                print(f"[Retry] Attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
    raise last_exc
