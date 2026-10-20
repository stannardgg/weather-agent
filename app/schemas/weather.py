"""横切层：天气数据的归一化契约。

这是「供应商无关」的对外形状：上层（M4 的 Agent）只认这些字段，
换天气供应商时这里不动，只改 tools/weather.py。
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ResolvedLocation(BaseModel):
    """城市名解析后的地点信息。回传给用户，便于澄清同名城市。"""

    name: str
    lat: float
    lon: float
    location_id: str | None = None
    adm1: str | None = None  # 省级
    adm2: str | None = None  # 市级
    country: str | None = None


class CurrentWeather(BaseModel):
    """实时天气。

    单位约定（供应商实现必须换算到这里，上层不应再关心原始单位）：
    - 温度一律**摄氏度**；风速一律 **km/h**（上游给 m/s，由 _speed_to_kmh 换算）
    - 湿度、降水概率一律 **0–100 的整数百分比**（上游给 0–1 比例，由 _ratio_to_pct 换算）
    - 降水量一律 **mm**
    """

    observed_at: str | None = None
    temperature: float | None = None
    feels_like: float | None = None
    condition: str | None = None
    humidity: int | None = None
    wind_direction: str | None = None
    wind_speed: float | None = None
    precipitation: float | None = None
    pressure: float | None = None
    uv_index: int | None = None  # 2026-10-12 新增：上游 uvIndex 一直有值，此前被丢弃


class DailyForecast(BaseModel):
    """单日预报。precip_probability 是回答「要不要带伞」的关键字段。

    单位约定同 CurrentWeather：温度 °C、风速 km/h、湿度与降水概率为 0–100 整数百分比、
    降水量 mm。`date` 是**东八区**日期（上游给的是 UTC 的 forecastStartTime）。
    """

    date: str
    temp_min: float | None = None
    temp_max: float | None = None
    condition_day: str | None = None
    condition_night: str | None = None
    precip_probability: int | None = None
    precipitation: float | None = None
    humidity: int | None = None
    uv_index: int | None = None
    sunrise: str | None = None
    sunset: str | None = None
    wind_direction_day: str | None = None
    wind_speed_day: float | None = None


class LifeIndex(BaseModel):
    """生活指数：数据源已经算好的结论（如「带伞指数：建议带伞」）。

    优先级高于让模型自行从降水量推断，符合 CLAUDE.md §8.3 的防幻觉要求。
    """

    type: str
    name: str
    level: str | None = None
    category: str | None = None
    text: str | None = None


class WeatherResult(BaseModel):
    """get_weather() 的返回值。自带来源与时间信息，便于回答中标注依据。"""

    provider: str
    query_city: str
    resolved: ResolvedLocation
    current: CurrentWeather | None = None
    daily: list[DailyForecast] = Field(default_factory=list)
    indices: list[LifeIndex] = Field(default_factory=list)
    target: DailyForecast | None = None  # 指定 date 时命中的那一条
    cached: bool = False
    fetched_at: str = Field(default_factory=_now_iso)
