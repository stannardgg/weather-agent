"""M2 天气工具测试。

网络层用 `httpx.MockTransport` 整体替换，测试不访问真实网络，
因此不受和风天气的每日配额、断网或限流影响。

⚠️ 样本的真实性等级**不同**，这决定了它们能证明什么（详见下方注释）：
`CURRENT_BODY` / `DAILY_BODY` / `INDICES_BODY` 是 2026-10-12 从真实接口**逐字节抓取**的，
因此它们**能**证明「线上字段名就是这些」——这正是 M2 此前唯一无法验证的部分。
`GEO_BODY` 仍是按官方文档构造的：该接口当前被上游安全策略拒绝
（403 Security Restriction），拿不到成功响应。
"""

import httpx
import pytest

from app.core.config import settings
from app.schemas.common import ErrorCode
from app.tools import weather
from app.tools.weather import (
    CityNotFoundError,
    QWeatherProvider,
    UnsupportedProviderError,
    UpstreamCodeError,
    WeatherAuthError,
    WeatherError,
    WeatherNotConfiguredError,
    WeatherUpstreamError,
)

GEO_BODY = {  # ⚠️ 构造样本，未经实测（接口当前返回 403，拿不到成功响应）
    "code": "200",
    "location": [
        {
            "name": "上海",
            "id": "101020100",
            "lat": "31.23171",
            "lon": "121.47264",
            "adm1": "上海市",
            "adm2": "上海",
            "country": "中国",
        }
    ],
}

# ✅ 实测真实响应（上海）。三个与旧认知相反的关键点，注释在此以免日后被"改回去"：
#    ① 是**扁平顶层**，没有 v7 的 {"now": {...}} 包装；
#    ② 带单位的量是**嵌套对象**（temperature.value / 22.51），不是字符串 "22"；
#    ③ humidity 是 **0–1 比例**（0.71 = 71%），不是百分数。
CURRENT_BODY = {
    "metadata": {
        "tag": "1e99a8f6759574bc7d2d9364d4abeea6d77dfb433ef04046060fc8011d096046",
        "attributions": ["https://developer.qweather.com/attribution.html"],
    },
    "condition": {"text": "阴", "code": "104"},
    "temperature": {"value": 22.51, "unit": "°C"},
    "feelsLike": {"value": 24.25, "unit": "°C"},
    "humidity": 0.71,
    "wind": {
        "direction": {"degree": 114, "compass": "ese"},
        "speed": {"value": 0.47, "unit": "m/s"},
        "scale": 0,
    },
    "windGust": {"value": 5.37, "unit": "m/s"},
    "precipitation": {
        "amount": {"value": 0, "unit": "mm"},
        "intensity": {"value": 0, "unit": "mm/h"},
        "type": "none",
    },
    "pressure": {"value": 1017.98, "unit": "hPa"},
    "visibility": {"value": 16380, "unit": "m"},
    "dewPoint": {"value": 16.92, "unit": "°C"},
    "cloudCover": 0.52,
    "uvIndex": 4,
}

# ✅ 实测真实响应（上海，7 天里取前 2 天）。
# ⚠️ 日期是 **UTC**：'2026-10-11T16:00Z' 相当于北京时间 10-12 00:00，解析后应得
#    date == "2026-10-12"。若哪天有人把时区转换去掉，下方断言会立刻炸——这是刻意的。
DAILY_BODY = {
    "metadata": {
        "tag": "ba5d21295f5a64037f447e05680021f44df2bd30c575c57fba5d5da453f127f1",
        "attributions": ["https://developer.qweather.com/attribution.html"],
    },
    "days": [
        {
            "forecastStartTime": "2026-10-11T16:00Z",
            "forecastEndTime": "2026-10-12T16:00Z",
            "astro": {
                "sunrise": "2026-10-11T21:55Z",
                "sunset": "2026-10-12T09:28Z",
                "astronomicalDawn": "2026-10-11T20:35Z",
                "nauticalDawn": "2026-10-11T21:03Z",
                "civilDawn": "2026-10-11T21:31Z",
                "astronomicalDusk": "2026-10-12T10:48Z",
                "nauticalDusk": "2026-10-12T10:20Z",
                "civilDusk": "2026-10-12T09:52Z",
                "solarNoon": "2026-10-12T03:41Z",
                "solarMidnight": "2026-10-11T15:41Z",
                "moonrise": "2026-10-11T23:22Z",
                "moonset": "2026-10-12T10:11Z",
                "moonTransit": "2026-10-12T04:47Z",
                "moonUnderfoot": "2026-10-12T16:47Z",
                "moonPhase": "waxing-crescent",
            },
            "temperatureMax": {"value": 25.07, "unit": "°C"},
            "temperatureMin": {"value": 17.09, "unit": "°C"},
            "temperatureAvg": {"value": 22.09, "unit": "°C"},
            "uvIndexMax": 5,
            "daytime": {
                "forecastStartTime": "2026-10-11T23:00Z",
                "forecastEndTime": "2026-10-12T11:00Z",
                "condition": {"text": "多云", "code": "101"},
                "temperatureMax": {"value": 25.07, "unit": "°C"},
                "temperatureMin": {"value": 17.97, "unit": "°C"},
                "humidity": 0.7,
                "wind": {
                    "direction": {"degree": 340, "compass": "nnw"},
                    "speed": {"value": 2.19, "unit": "m/s"},
                    "scale": 2,
                },
                "windGustMax": {"value": 7.45, "unit": "m/s"},
                "precipitation": {
                    "amount": {"value": 0, "unit": "mm"},
                    "probability": 0,
                    "type": "none",
                },
                "cloudCover": 0.65,
            },
            "nighttime": {
                "forecastStartTime": "2026-10-12T11:00Z",
                "forecastEndTime": "2026-10-12T23:00Z",
                "condition": {"text": "晴间多云", "code": "103"},
                "temperatureMax": {"value": 22.98, "unit": "°C"},
                "temperatureMin": {"value": 18.77, "unit": "°C"},
                "humidity": 0.89,
                "wind": {
                    "direction": {"degree": 293, "compass": "wnw"},
                    "speed": {"value": 1.86, "unit": "m/s"},
                    "scale": 2,
                },
                "windGustMax": {"value": 4.15, "unit": "m/s"},
                "precipitation": {
                    "amount": {"value": 0, "unit": "mm"},
                    "probability": 0,
                    "type": "none",
                },
                "cloudCover": 0.32,
            },
        },
        {
            # 为控制篇幅，第二天只保留解析涉及的键（形状与第一天完全一致，仍是实测值）
            "forecastStartTime": "2026-10-12T16:00Z",
            "astro": {
                "sunrise": "2026-10-12T21:56Z",
                "sunset": "2026-10-13T09:27Z",
            },
            "temperatureMax": {"value": 27.16, "unit": "°C"},
            "temperatureMin": {"value": 18.77, "unit": "°C"},
            "uvIndexMax": 5,
            "daytime": {
                "condition": {"text": "晴间多云", "code": "103"},
                "humidity": 0.6,
                "wind": {
                    "direction": {"degree": 333, "compass": "nnw"},
                    "speed": {"value": 2.36, "unit": "m/s"},
                },
                "precipitation": {
                    "amount": {"value": 0, "unit": "mm"},
                    "probability": 0,
                    "type": "none",
                },
            },
            "nighttime": {
                "condition": {"text": "晴", "code": "100"},
                "humidity": 0.72,
            },
        },
    ],
}

# 昆明 2026-10-12 的**实测抓取值**，只保留当时真正取到的字段（未实测的不编造）。
# 存在的理由：上海那两天恰逢无雨，probability / precipitation 全是 0——
# 而 0 乘任何系数都还是 0，**验证不了** 0–1 → 百分比 的换算。这个样本专补这个洞。
RAINY_DAILY_BODY = {
    "days": [
        {
            "forecastStartTime": "2026-10-12T16:00Z",
            "daytime": {
                "humidity": 0.72,
                "precipitation": {
                    "amount": {"value": 5.71, "unit": "mm"},
                    "probability": 0.7,
                    "type": "rain",
                },
            },
            "nighttime": {
                "humidity": 0.78,
                "precipitation": {
                    "amount": {"value": 0, "unit": "mm"},
                    "probability": 0.78,
                    "type": "rain",
                },
            },
        }
    ]
}

# ✅ 实测真实响应。注意两点：① 这是**旧信封** `{"code": "200", ...}`，不是 v1 的
#    RFC 7807，所以 _parse_payload 的旧信封分支在这里生效；② type 是**必填**参数，
#    type=0 一次返回 16 类 × 3 天 = 48 条（此处截取 3 条）。
INDICES_BODY = {
    "code": "200",
    "updateTime": "2026-10-12T11:59+08:00",
    "fxLink": "https://www.qweather.com/indices/huangpu-101020400.html",
    "daily": [
        {
            "date": "2026-10-12",
            "type": "1",
            "name": "运动指数",
            "level": "2",
            "category": "较适宜",
            "text": "阴天，较适宜进行各种运动。",
        },
        {
            "date": "2026-10-12",
            "type": "2",
            "name": "洗车指数",
            "level": "1",
            "category": "适宜",
            "text": "适宜洗车，未来持续两天无雨天气较好，适合擦洗汽车。",
        },
        {
            "date": "2026-10-12",
            "type": "3",
            "name": "穿衣指数",
            "level": "5",
            "category": "舒适",
            "text": "建议着长袖T恤、衬衫加单裤等服装。",
        },
    ],
    "refer": {"sources": ["QWeather"], "license": ["QWeather Developers License"]},
}


class Recorder:
    """记录请求并允许按路径指定状态码。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, list[int]] = {}  # path -> 依次返回的状态码队列

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        queued = self.overrides.get(path)
        if queued:
            status = queued.pop(0)
            if status >= 400:
                return httpx.Response(status, request=request, text="upstream error")

        if path.startswith("/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        if "/daily/" in path:
            return httpx.Response(200, request=request, json=DAILY_BODY)
        if "/indices/" in path:
            return httpx.Response(200, request=request, json=INDICES_BODY)
        return httpx.Response(404, request=request, text="not found")

    @property
    def count(self) -> int:
        return len(self.requests)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例独立：清缓存、重置 provider 单例、替换退避等待。"""
    weather.clear_cache()
    monkeypatch.setattr(weather, "_provider", None)

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(weather, "_sleep", _instant)

    monkeypatch.setattr(settings, "weather_api_host", "test.qweatherapi.com")
    monkeypatch.setattr(settings, "weather_api_key", "test-key")
    monkeypatch.setattr(settings, "weather_provider", "qweather")
    monkeypatch.setattr(settings, "weather_max_retries", 2)
    yield
    weather.clear_cache()
    monkeypatch.setattr(weather, "_provider", None)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def provider(recorder):
    """注入 MockTransport 的 provider。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler))
    return QWeatherProvider(client=client)


@pytest.fixture
def patched_get_provider(monkeypatch, provider):
    """把门面里的 provider 换成注入了 MockTransport 的实例。"""
    monkeypatch.setattr(weather, "get_provider", lambda: provider)
    return provider


# --- 解析与归一化 ---------------------------------------------------------


async def test_resolve_city_parses_coordinates(provider):
    location = await provider.resolve_city("上海")
    assert location.name == "上海"
    assert location.lat == pytest.approx(31.23171)
    assert location.lon == pytest.approx(121.47264)
    assert location.location_id == "101020100"
    assert location.adm1 == "上海市"


async def test_resolve_city_caches_second_lookup(provider, recorder):
    await provider.resolve_city("上海")
    first = recorder.count
    await provider.resolve_city("上海")
    assert recorder.count == first, "同一城市第二次解析应命中缓存，不再请求"


async def test_resolve_city_empty_result_raises_city_not_found(recorder, monkeypatch):
    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"code": "200", "location": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(empty_handler))
    provider = QWeatherProvider(client=client)
    with pytest.raises(CityNotFoundError):
        await provider.resolve_city("不存在的地方")


async def test_normalizes_current_and_daily(patched_get_provider):
    """逐字段校准：断言值全部来自 2026-10-12 实测的真实响应。"""
    result = await weather.get_weather("上海")

    assert result.provider == "qweather"
    assert result.resolved.name == "上海"
    assert result.current is not None

    current = result.current
    assert current.condition == "阴"  # condition.text，不是顶层 text
    assert current.temperature == pytest.approx(22.51)  # temperature.value
    assert current.feels_like == pytest.approx(24.25)  # feelsLike.value
    assert current.humidity == 71, "上游给 0.71，契约要求 0–100 百分比"
    assert current.wind_direction == "东南偏东", "由 degree 114 换算（与上游 compass=ese 一致）"
    assert current.wind_speed == pytest.approx(1.69), "0.47 m/s × 3.6 = km/h"
    assert current.precipitation == 0.0  # precipitation.amount.value
    assert current.pressure == pytest.approx(1017.98)  # pressure.value
    assert current.uv_index == 4  # uvIndex
    assert current.observed_at is None, "v1 实测没有观测时间字段，不应编造一个"

    # 日期必须先把 UTC 转成东八区，否则整条预报集体差一天
    assert [d.date for d in result.daily] == ["2026-10-12", "2026-10-13"]

    first = result.daily[0]
    assert first.temp_max == pytest.approx(25.07)
    assert first.temp_min == pytest.approx(17.09)
    assert first.condition_day == "多云"  # daytime.condition.text
    assert first.condition_night == "晴间多云"  # nighttime.condition.text
    assert first.humidity == 70  # daytime.humidity 0.7 → 70
    assert first.uv_index == 5  # uvIndexMax
    assert first.sunrise == "2026-10-12 05:55"  # 2026-10-11T21:55Z → 东八区
    assert first.sunset == "2026-10-12 17:28"
    assert first.wind_direction_day == "西北偏北"  # degree 340
    assert first.wind_speed_day == pytest.approx(7.88)  # 2.19 m/s × 3.6


async def test_normalizes_life_indices(patched_get_provider):
    result = await weather.get_weather("上海")
    names = {i.name for i in result.indices}
    assert names == {"运动指数", "洗车指数", "穿衣指数"}
    sport = next(i for i in result.indices if i.type == "1")
    assert sport.category == "较适宜"
    assert sport.level == "2"


async def test_target_matches_requested_date(patched_get_provider):
    result = await weather.get_weather("上海", "2026-10-13")
    assert result.target is not None
    assert result.target.date == "2026-10-13"
    assert result.target.temp_max == pytest.approx(27.16)
    assert result.target.condition_day == "晴间多云"


async def test_target_is_none_when_date_out_of_range(patched_get_provider):
    result = await weather.get_weather("上海", "2099-01-01")
    assert result.target is None


async def test_key_goes_into_header_not_query(provider, recorder):
    await provider.resolve_city("上海")
    request = recorder.requests[0]
    assert request.headers.get("X-QW-Api-Key") == "test-key"
    assert "key" not in request.url.params, "密钥不得出现在查询参数里（会进访问日志）"


# --- 缓存 --------------------------------------------------------------


async def test_second_call_hits_cache(patched_get_provider, recorder):
    await weather.get_weather("上海")
    calls_after_first = recorder.count
    result = await weather.get_weather("上海")

    assert recorder.count == calls_after_first, "第二次应完全命中缓存，不发任何请求"
    assert result.cached is True


async def test_different_date_is_a_different_cache_key(patched_get_provider, recorder):
    await weather.get_weather("上海")
    calls_after_first = recorder.count
    await weather.get_weather("上海", "2026-10-13")
    assert recorder.count > calls_after_first


async def test_cache_stats_count_hits(patched_get_provider):
    await weather.get_weather("上海")
    await weather.get_weather("上海")
    stats = weather.cache_stats()
    assert stats["hits"] >= 1


async def test_cached_result_is_a_copy(patched_get_provider):
    first = await weather.get_weather("上海")
    first.query_city = "被改过了"
    second = await weather.get_weather("上海")
    assert second.query_city == "上海", "缓存对象不应被调用方就地修改污染"


# --- 配置与错误 --------------------------------------------------------


async def test_blank_city_raises_param_error(patched_get_provider):
    with pytest.raises(WeatherError) as exc:
        await weather.get_weather("   ")
    assert exc.value.code == ErrorCode.PARAM_ERROR


async def test_missing_host_raises_not_configured(monkeypatch, recorder):
    monkeypatch.setattr(settings, "weather_api_host", "")
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler))
    with pytest.raises(WeatherNotConfiguredError):
        await QWeatherProvider(client=client).resolve_city("上海")


async def test_missing_key_raises_not_configured(monkeypatch, recorder):
    monkeypatch.setattr(settings, "weather_api_key", "")
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler))
    with pytest.raises(WeatherNotConfiguredError):
        await QWeatherProvider(client=client).resolve_city("上海")


async def test_unsupported_provider_raises(monkeypatch):
    monkeypatch.setattr(settings, "weather_provider", "openweather")
    with pytest.raises(UnsupportedProviderError):
        weather.get_provider()


async def test_upstream_error_code_raises(recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"code": "401", "message": "Unauthorized"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError):
        await QWeatherProvider(client=client).resolve_city("上海")


# --- 重试策略 ----------------------------------------------------------


async def test_4xx_is_not_retried(recorder, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(400, request=request, text="bad request")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError):
        await QWeatherProvider(client=client).resolve_city("上海")
    assert recorder.count == 1, "4xx 是请求本身的问题，重试只会白烧配额"


async def test_5xx_is_retried_then_succeeds(provider, recorder):
    recorder.overrides["/geo/v2/city/lookup"] = [500]
    location = await provider.resolve_city("上海")
    assert location.name == "上海"
    assert recorder.count == 2, "第 1 次 500、第 2 次成功，共 2 次请求"


async def test_retry_exhausted_raises_after_all_attempts(provider, recorder):
    recorder.overrides["/geo/v2/city/lookup"] = [500, 500, 500]
    with pytest.raises(WeatherUpstreamError):
        await provider.resolve_city("上海")
    assert recorder.count == 3, "max_retries=2 → 共尝试 3 次"


# --- 降级 --------------------------------------------------------------


async def test_indices_failure_degrades_to_empty(patched_get_provider, recorder):
    """生活指数是附加信息：上游挂了也不能把整个查询拖垮。"""
    recorder.overrides["/v7/indices/3d"] = [500, 500, 500]
    result = await weather.get_weather("上海")
    assert result.indices == []
    assert result.current is not None, "实时天气仍应返回"
    assert len(result.daily) == 2, "每日预报仍应返回"


async def test_daily_without_date_is_skipped(recorder):
    """个别条目缺日期时跳过它，但**不能因为一条坏数据丢掉整份预报**。"""
    body = {
        "days": [
            {"temperatureMax": {"value": 25.0}},  # 没有任何日期字段
            {"forecastStartTime": "2026-10-12T16:00Z", "temperatureMax": {"value": 23.0}},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        return httpx.Response(200, request=request, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QWeatherProvider(client=client)
    result = await provider.fetch_weather("上海")
    assert [d.date for d in result.daily] == ["2026-10-13"], "缺日期的条目应被跳过而不是补假日期"


def path_ok(request: httpx.Request, prefix: str) -> bool:
    return request.url.path.startswith(prefix)


# --- 真实上游错误格式（RFC 7807）----------------------------------------
#
# 下面的样本是 2026-10-12 用假 key 打真实服务器抓回来的**原样**响应，
# 不是构造的。和风已把错误体从 {"code": "401"} 改成 application/problem+json。

PROBLEM_401 = {
    "error": {
        "status": 401,
        "type": "https://dev.qweather.com/docs/resource/error-code/#unauthorized",
        "title": "Unauthorized",
        "detail": "Authentication failed, check your KEY/Token or Host.",
    }
}
PROBLEM_403 = {
    "error": {
        "status": 403,
        "type": "https://dev.qweather.com/docs/resource/error-code/#invalid-host",
        "title": "Invalid Host",
        "detail": "An invalid or unauthorized API Host.",
    }
}


async def test_problem_json_401_is_reported_as_config_error_not_transient(recorder):
    """401 是配置错误：必须给「去改 .env」的提示，而不是「稍后重试」。"""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(401, request=request, json=PROBLEM_401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherAuthError) as exc:
        await QWeatherProvider(client=client).resolve_city("上海")

    assert exc.value.http_status == 503, "配置问题用 503，不用 502（502 会暗示是上游临时故障）"
    assert exc.value.code == ErrorCode.INTERNAL_ERROR
    assert "WEATHER_API_HOST" in str(exc.value) and "WEATHER_API_KEY" in str(exc.value)
    assert recorder.count == 1, "认证失败重试无意义，只应发一次请求"


async def test_problem_json_403_invalid_host_is_not_retried(recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(403, request=request, json=PROBLEM_403)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherAuthError) as exc:
        await QWeatherProvider(client=client).resolve_city("上海")
    assert "Invalid Host" in str(exc.value), "上游的原始描述要保留，否则无法判断是 Host 还是 KEY 的问题"
    assert recorder.count == 1


async def test_error_body_is_not_mistaken_for_success(recorder):
    """HTTP 200 但 body 是 error：**不能**当成功。

    这是本项目最危险的一类故障——解析器"假装成功"后 current / daily 全为 None，
    上层会以为「天气就是这样」，用户拿到「今天气温 None 度」。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(200, request=request, json=PROBLEM_401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherAuthError):
        await QWeatherProvider(client=client).resolve_city("上海")


async def test_unrecognized_current_payload_raises_instead_of_all_none(recorder):
    """字段名全猜错时，必须响亮报错，而不是返回一个全 None 的 CurrentWeather。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json={"code": "200", "somethingElse": {}})
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json=DAILY_BODY)
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError) as exc:
        await QWeatherProvider(client=client).fetch_weather("上海")
    assert "未识别出任何字段" in str(exc.value)
    assert "somethingElse" in str(exc.value), "错误信息要带上顶层键，否则没法校准"


async def test_daily_missing_list_key_raises_but_empty_list_is_fine(recorder):
    """「键不存在」（猜错字段名）与「键存在但为空」是两回事。"""

    def missing(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json={"code": "200", "noSuchKey": []})
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(missing))
    with pytest.raises(WeatherUpstreamError) as exc:
        await QWeatherProvider(client=client).fetch_weather("上海")
    assert "未找到预报列表" in str(exc.value)

    def empty(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json={"code": "200", "daily": []})
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client2 = httpx.AsyncClient(transport=httpx.MockTransport(empty))
    result = await QWeatherProvider(client=client2).fetch_weather("上海")
    assert result.daily == [], "键存在但是空列表属于合法响应，不该报错"


async def test_indices_missing_key_degrades_quietly(recorder):
    """生活指数是降级项：字段名没认出来只记 warning，不拖垮主流程。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json=DAILY_BODY)
        return httpx.Response(200, request=request, json={"code": "200", "noSuchKey": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await QWeatherProvider(client=client).fetch_weather("上海")
    assert result.indices == []
    assert result.current is not None and len(result.daily) == 2


async def test_upstream_business_code_carries_attribute(recorder):
    """旧信封的业务错误码要能以属性读取，而不是靠 str(exc) 搜数字。

    用 402 而不是 404：404 会被 resolve_city 有意翻译成「城市不存在」（下一个用例覆盖）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"code": "402", "message": "quota exceeded"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamCodeError) as exc:
        await QWeatherProvider(client=client).resolve_city("上海")
    assert exc.value.upstream_code == "402"


async def test_city_not_found_via_upstream_404_code(recorder):
    """上游 code=404 应被翻译成「城市不存在」，而不是笼统的上游错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"code": "404", "message": "no data"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(CityNotFoundError):
        await QWeatherProvider(client=client).resolve_city("不存在的地方")


# --- 路径常量回归护栏 ---------------------------------------------------
#
# 这些常量是 2026-10-12 用「401/403=路由存在、404=路由不存在」判据对真实服务器
# 探测出来的结论。写成断言是为了防止有人凭直觉又改回旧教程里的写法。


def test_path_constants_match_probed_reality():
    assert weather.CURRENT_PATH == "/weather/v1/current/{lat}/{lon}"
    assert weather.DAILY_PATH == "/weather/v1/daily/{lat}/{lon}"
    assert weather.GEO_CITY_LOOKUP_PATH == "/geo/v2/city/lookup"
    assert weather.INDICES_PATH == "/v7/indices/3d", (
        "实测 /indices/v1/3d 返回 404（路由不存在），正确路由在 v7 命名空间下"
    )


async def test_indices_uses_v7_namespace_and_lon_lat_param(provider, recorder):
    """v7 接口用 location 查询参数，且经纬度是「经度在前」。"""
    location = await provider.resolve_city("上海")
    await provider.fetch_indices(location)

    request = recorder.requests[-1]
    assert request.url.path == "/v7/indices/3d"
    assert request.url.params["location"] == "121.47264,31.23171"
    assert request.url.params["type"] == "0", "缺 type 会直接 400 Missing Parameter（实测）"
    assert "latitude" not in request.url.params, "v7 命名空间不认 latitude/longitude 参数"


# --- 单位换算与风向（2026-10-12 新增）----------------------------------
#
# 单独测这些纯函数，是因为它们是 v1 形状与项目契约之间唯一的翻译层：
# 上游给 0–1 比例 / m/s / degree，契约要 0–100 百分比 / km/h / 中文方位。
# 算错不会抛异常，只会**静默给出错误数值**（「湿度 1%」「风速 0.47 km/h，实际 1.69」）。


def test_speed_converts_ms_to_kmh():
    assert weather._speed_to_kmh(1, "m/s") == pytest.approx(3.6)
    assert weather._speed_to_kmh(0.47, "m/s") == pytest.approx(1.69)
    assert weather._speed_to_kmh(10, "km/h") == pytest.approx(10), "已是 km/h 就不该再乘"
    assert weather._speed_to_kmh(None, "m/s") is None
    assert weather._speed_to_kmh("abc", "m/s") is None


def test_ratio_becomes_percentage_across_both_scales():
    assert weather._ratio_to_pct(0.71) == 71
    assert weather._ratio_to_pct(0.7) == 70
    assert weather._ratio_to_pct(0) == 0
    assert weather._ratio_to_pct(1) == 100, "0–1 尺度下 1 表示 100%"
    assert weather._ratio_to_pct(85) == 85, "上游若改回百分数，不该被算成 8500"
    assert weather._ratio_to_pct(None) is None


def test_wind_direction_matches_upstream_compass():
    """用上游自带的 compass 交叉验证中文换算（实测两个点：ese / nnw）。"""
    assert weather._wind_direction({"direction": {"degree": 114}}) == "东南偏东"  # ese
    assert weather._wind_direction({"direction": {"degree": 340}}) == "西北偏北"  # nnw
    assert weather._wind_direction({"direction": {"compass": "ese"}}) == "ese", "无 degree 时退回原值"
    assert weather._wind_direction(None) is None
    assert weather._wind_direction("not-a-dict") is None


async def test_rainy_day_ratio_is_converted_to_percentage(recorder):
    """上海样本恰好无雨（概率全 0），而 0 乘任何系数仍是 0——验证不了换算。

    昆明这天原本实测有雨，专门用来钉死「0.7 → 70」这一步。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        return httpx.Response(200, request=request, json=RAINY_DAILY_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await QWeatherProvider(client=client).fetch_weather("昆明")

    day = result.daily[0]
    assert day.date == "2026-10-13"
    assert day.precip_probability == 70, "0.7 是 70%，不是 1 也不是 0.7"
    assert day.precipitation == pytest.approx(5.71), "降水量本身就是 mm，不需换算"
    assert day.humidity == 72


# --- 假成功防线加固（2026-10-12）---------------------------------------
#
# 校准过程中发现原防线有个洞：字段取到一个 dict 时 `str(dict)` 会产生**非空**字符串，
# 于是「一个字段都没认出来」被误判成「解析成功」——上层拿到的是「气温 None 度 +
# 天气条件是一坨 dict 字符串」。这不是假想，是校准前 `condition` 的真实行为。


async def test_nested_dict_value_does_not_count_as_parsed(recorder):
    """命中了一个 dict 值 ≠ 解析成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            # condition.text 本身是 dict：修复前会被 str() 成非空字符串骗过防线
            return httpx.Response(
                200, request=request, json={"condition": {"text": {"oops": "nested"}}}
            )
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json=DAILY_BODY)
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError) as exc:
        await QWeatherProvider(client=client).fetch_weather("上海")
    assert "未识别出任何字段" in str(exc.value)


async def test_v7_shaped_current_raises_instead_of_passing(recorder):
    """v7 形状（{"now": {...}}）在 v1 解析下必须报错，而不是"看着有值"地混过去。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(
                200, request=request, json={"code": "200", "now": {"temp": "22", "text": "多云"}}
            )
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json=DAILY_BODY)
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError) as exc:
        await QWeatherProvider(client=client).fetch_weather("上海")
    assert "未识别出任何字段" in str(exc.value)


async def test_daily_all_dates_unparseable_raises(recorder):
    """列表拿到了但一条日期都解析不出 → 报错，而不是静默返回空预报。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if path_ok(request, "/geo/"):
            return httpx.Response(200, request=request, json=GEO_BODY)
        if "/current/" in request.url.path:
            return httpx.Response(200, request=request, json=CURRENT_BODY)
        if "/daily/" in request.url.path:
            return httpx.Response(200, request=request, json={"days": [{"weird": 1}, {"weird": 2}]})
        return httpx.Response(200, request=request, json=INDICES_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherUpstreamError) as exc:
        await QWeatherProvider(client=client).fetch_weather("上海")
    assert "未能解析出日期" in str(exc.value)


# 2026-10-12 用真实凭据抓回的**原样**错误体：城市搜索被上游安全策略拒绝。
# 这是本项目实际遇到过的第三种 403（另两种是共享 Host 的 Invalid Host 与占位 key 的 401）。
PROBLEM_403_SECURITY = {
    "error": {
        "status": 403,
        "type": "https://dev.qweather.com/docs/resource/error-code/#security-restriction",
        "title": "Security Restriction",
        "detail": "Request is denied by security restriction settings.",
    }
}


async def test_security_restriction_403_goes_to_auth_branch(recorder):
    """GeoAPI 的 403 必须走「配置/权限」分支，而不是被当成上游临时故障去重试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        return httpx.Response(403, request=request, json=PROBLEM_403_SECURITY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherAuthError) as exc:
        await QWeatherProvider(client=client).resolve_city("上海")
    assert "Security Restriction" in str(exc.value), "保留上游原始描述，才知道该去控制台改什么"
    assert exc.value.http_status == 503
    assert recorder.count == 1, "权限问题重试无意义"
