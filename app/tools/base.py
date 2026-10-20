"""【L3】工具层的统一契约（CLAUDE.md §5 tools/base.py）。

定义「一个天气供应商必须提供什么」，让上层只依赖抽象而不依赖和风本身。
"""

from typing import Protocol, runtime_checkable

from app.schemas.weather import ResolvedLocation, WeatherResult


@runtime_checkable
class WeatherProvider(Protocol):
    """天气供应商协议。任何供应商实现这三个动作即可被上层使用。"""

    name: str

    async def resolve_city(self, city: str) -> ResolvedLocation:
        """把用户输入的城市名解析成地点（经纬度 + 行政信息）。"""
        ...

    async def fetch_current(self, location: ResolvedLocation) -> object:
        """取实时天气（归一化后的 CurrentWeather，或 None）。"""
        ...

    async def fetch_weather(
        self, city: str, date: str | None = None
    ) -> WeatherResult:
        """一次完整查询：解析城市 → 取实时 / 每日预报 / 生活指数 → 归一化。"""
        ...
