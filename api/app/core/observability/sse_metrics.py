"""Dependency-free operational counters and latency samples per API instance."""

from collections import Counter, defaultdict, deque
from threading import Lock


class RuntimeMetrics:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=2048))
        self._lock = Lock()

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._samples[name].append(float(value))

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            samples = {name: list(values) for name, values in self._samples.items()}
        observations = {}
        for name, values in samples.items():
            ordered = sorted(values)
            if ordered:
                observations[name] = {
                    "count": len(ordered),
                    "avg": round(sum(ordered) / len(ordered), 3),
                    "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
                    "max": round(ordered[-1], 3),
                }
        return {"counters": counters, "observations": observations}


runtime_metrics = RuntimeMetrics()
