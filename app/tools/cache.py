"""【L1/L3】进程内 TTL 缓存。

为什么用 time.monotonic 而不是 time.time：系统时间被 NTP 校正或手工改动时，
time.time() 会跳变，导致缓存提前失效或永不过期；monotonic 只增不减，适合做间隔计时。

为什么缓存要带 stats：和风免费版有每日调用上限，hits/misses 能直接告诉你省了多少次调用。
"""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Entry:
    value: Any
    expires_at: float


@dataclass
class TTLCache:
    default_ttl: int = 900
    _store: dict[str, _Entry] = field(default_factory=dict)
    _hits: int = 0
    _misses: int = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        if entry.expires_at <= time.monotonic():
            # 过期即删，避免缓存无限增长
            del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        seconds = self.default_ttl if ttl is None else ttl
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + seconds)

    def clear(self) -> None:
        self._store.clear()

    def purge_expired(self) -> int:
        now = time.monotonic()
        expired = [k for k, e in self._store.items() if e.expires_at <= now]
        for key in expired:
            del self._store[key]
        return len(expired)

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._store)}
