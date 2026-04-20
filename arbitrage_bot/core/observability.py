from collections import Counter
from datetime import datetime
from datetime import timezone
from threading import Lock

_counters = Counter()
_lock = Lock()
_started_at = datetime.now(timezone.utc)


def get_started_at():
    return _started_at


def incr_counter(name, amount=1):
    with _lock:
        _counters[str(name)] += int(amount)


def snapshot_counters():
    with _lock:
        return dict(_counters)


def snapshot_and_reset_counters():
    with _lock:
        snapshot = dict(_counters)
        _counters.clear()
        return snapshot


def reset_counters():
    with _lock:
        _counters.clear()