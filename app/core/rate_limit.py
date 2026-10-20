"""【L1】进程内滑动窗口限流（M6-Q1 方案 A）。

## 为什么是「滑动窗口」而不是「固定窗口」

固定窗口按整点重置（如「每分钟第 0 秒清零」），在窗口交界处会出现「上一分钟末尾 +
这一分钟开头」连续打满的瞬间翻倍——攻击者掐着点发就能绕过去。滑动窗口记录每次请求
的时间戳，只数「最近 N 秒内」的请求，任意时刻都平滑、无边界突刺。

## 为什么进程内、按 IP

本项目 M7 是单机/私有化部署，进程内内存即可，无需引入 Redis（多实例才需要共享计数）。
按 IP 限流是最通用可靠的防刷手段：登录/注册接口在「还没登录」时根本没有 user_id 可查，
只能按 IP；已登录接口按 IP 也足够挡住「单个来源刷爆」。

## 线程安全说明

本类只被 async 中间件在事件循环里调用（协作式、单线程），`deque` 的 append/popleft
是原子操作，因此不需要加锁。若未来改成多线程调用，再补 `threading.Lock`。
"""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    """按 key 记时间戳、判断是否放行。

    内存占用 = 活跃 key 数 × 每个 key 一个 deque。key 是「IP:分组」，企业内网 IP
    数量有限，可接受。过期的请求时间戳在每次 `allow` 时被 popleft 清理。
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """返回 True 表示放行（并记录本次），False 表示超限拒绝。

        时间戳用 `time.monotonic()`（单调时钟）：它不受系统时间回拨/调整影响，
        而 `time.time()` 在 NTP 校时或手动改时间时会跳变，导致窗口计算错乱。
        """
        now = time.monotonic()
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        else:
            cutoff = now - window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()

        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        """清空全部计数（供测试隔离）。"""
        self._hits.clear()
