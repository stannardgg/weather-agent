"""【L3】天气工具：和风天气（QWeather）实现（CLAUDE.md §9）。

对外只暴露 `get_weather(city, date)`；换供应商只需重写本文件，上层零改动。

和风已从 v7 + LocationID 迁移到 v1 + 经纬度，**两版的数据形状完全不同**
（v7：扁平 / 字符串 / 百分比 / 中文风向 / `fxDate`；
  v1：嵌套 / 数值带单位对象 / 0–1 比例 / 英文方位 / UTC `forecastStartTime`）。
本文件的解析层已于 2026-10-12 用**真实凭据**逐字段校准，实测证据记在文件顶部注释里。

⚠️ 唯一未通的是城市搜索 `/geo/v2/city/lookup`（403 Security Restriction）——
那是上游凭据权限问题，不是代码问题。届时「城市名 → 经纬度」这一步会抛
WeatherAuthError，处置办法见文件顶部注释。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core import metrics as metrics_module
from app.core.config import settings
from app.core.exceptions import AppError
from app.schemas.common import ErrorCode
from app.schemas.weather import (
    CurrentWeather,
    DailyForecast,
    LifeIndex,
    ResolvedLocation,
    WeatherResult,
)
from app.tools.cache import TTLCache

logger = logging.getLogger(__name__)

# 退避等待抽成模块级引用：测试里替换成「立即返回」，避免重试用例真的要等 0.5s+1s
_sleep = asyncio.sleep

# ---------------------------------------------------------------------------
# 路径常量（2026-10-12 用**真实凭据**完成校准）
#
# 判据（沿用假 key 阶段的约定，已证明有效）：
#   HTTP 200     = 通了，可以开始校准字段名
#   HTTP 401/403 = 请求已到达认证/授权层 → 路由真实存在
#   HTTP 404     = 路由未注册 → 该路径不存在
#   HTTP 400     = 已通过鉴权与安全校验，只是参数不全 → **路由与权限都正常**
#
#   路径                                  实测                          判读
#   /weather/v1/current/{lat}/{lon}       200                           ✅ 可用，字段名已校准
#   /weather/v1/daily/{lat}/{lon}         200                           ✅ 可用，字段名已校准
#   /weather/v1/now/{lat}/{lon}           404                           ❌ 别把 v7 的 current 写成 v1 的 now
#   /v7/indices/3d                        200（带 type）                ✅ 可用
#   /v7/indices/3d                        400 Missing Parameter         ⚠️ type 是**必填**参数
#   /v7/indices/1d|7d                     403 / 404                     ⚠️ 本项目只用 3d
#   /indices/v1/3d                        404                           ❌ **原本的错误猜测**，已修正
#   /geo/v2/city/lookup                   403 Security Restriction      ⛔ 当前不可用（见下）
#   /geo/v2/city/lookup                   400 Missing Parameter         ⚠️ 不带 location 时；说明路由与鉴权本身正常
#   /geo/v1|v2/city/lookup                404                           ❌ 旧城市搜索路径已下线
#
# ⛔ 城市搜索当前不可用（403 Security Restriction）——**不是代码问题**：
#    同一个 Host、同一个 KEY 下，/weather/v1/* 与 /v7/indices/3d 都返回 200，
#    只有 /geo/v2/* 被拒。关键证据是「不带 location 时返回 400」：这说明该请求
#    已经走过了路径匹配与鉴权，是在**参数校验之后**的那道安全/权限关卡被拦下的。
#    官方错误码文档对 SECURITY RESTRICTION 的定义是「违反了你设置的请求限制」，
#    同类公开案例的结论是「GeoAPI 权限没开或未生效」。
#    处置：到控制台核对凭据的「API 限制 / 应用限制」，并等待权限生效
#    （官方称安全设置变更最长 1–2 小时生效）。详见 CLAUDE.md §13。
#
# 两级 Host 策略（假 key 阶段发现，真实 key 下依然成立）：
#      /weather/v1/*  → 共享 Host 返 401（可路由，只是 key 无效）
#      /geo/v2/*      → 共享 Host 返 403 Invalid Host（**必须项目专属 Host**）
# ---------------------------------------------------------------------------
GEO_CITY_LOOKUP_PATH = "/geo/v2/city/lookup"  # 路由存在；上游安全策略当前拒绝（见上）
CURRENT_PATH = "/weather/v1/current/{lat}/{lon}"  # ✅ 200，字段名已校准
DAILY_PATH = "/weather/v1/daily/{lat}/{lon}"  # ✅ 200，字段名已校准
INDICES_PATH = "/v7/indices/3d"  # ✅ 200（需带 type=0），字段名与既有代码一致

# ---------------------------------------------------------------------------
# 响应列表键（2026-10-12 已用真实响应体校准）
#
# 实测形状：current 是**扁平顶层**（无 now/current 包装），daily 用 "days"，
# indices 用 "daily"。旧键保留作兼容（v7 旧族接口仍可访问、形状不同），
# 但它们现在是「已排除的候选」而不再是「以防万一的兜底」。
#
# 注意：**取值助手会拒绝容器值**（见 `_to_str`）。这是有意的——
# 校准前 `condition` 命中的是 `{"text": "阴"}`，`str(dict)` 会产生非空字符串，
# 从而绕开 `_reject_all_none` 的假成功防线（实测中这个洞真实存在过）。
# ---------------------------------------------------------------------------
_DAILY_LIST_KEYS = ("days", "daily", "forecast")  # 实测 "days"
_INDICES_LIST_KEYS = ("daily", "indices")  # 实测 "daily"
_CURRENT_OBJECT_KEYS = ("now", "current")  # v1 实测无包装键 → 直接用 payload 本身


class WeatherError(AppError):
    """天气工具错误基类，默认按「上游不可用」处理（502 网关错误）。"""

    def __init__(
        self,
        message: str,
        code: int = ErrorCode.BIZ_ERROR,
        http_status: int = 502,
    ) -> None:
        super().__init__(message, code=code, http_status=http_status)


class WeatherNotConfiguredError(WeatherError):
    def __init__(self) -> None:
        super().__init__(
            "天气服务未配置：请在 .env 中设置 WEATHER_API_HOST 与 WEATHER_API_KEY",
            code=ErrorCode.INTERNAL_ERROR,
            http_status=503,
        )


class UnsupportedProviderError(WeatherError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            f"不支持的天气供应商「{provider}」，当前仅实现 qweather",
            code=ErrorCode.INTERNAL_ERROR,
            http_status=500,
        )


class CityNotFoundError(WeatherError):
    def __init__(self, city: str) -> None:
        super().__init__(
            f"未找到城市「{city}」，请检查名称或改用更大的行政区名称（如「上海」而不是「张江」）",
            code=ErrorCode.NOT_FOUND,
            http_status=404,
        )


class WeatherUpstreamError(WeatherError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            f"天气服务暂时不可用（{detail}），请稍后重试",
            code=ErrorCode.BIZ_ERROR,
            http_status=502,
        )


class WeatherAuthError(WeatherError):
    """上游拒绝认证或拒绝 Host（401 / 403）。

    刻意**不**继承 WeatherUpstreamError：这不是「上游抖了一下」，重试一万次也不会好，
    提示用户「稍后重试」是在误导他。真正要做的是去改 .env 里的 Host / KEY，
    所以给 503（服务端未正确配置）而不是 502（网关上游故障），并把修复动作写进消息。
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            "天气服务认证失败：请检查 .env 中的 WEATHER_API_HOST 与 WEATHER_API_KEY"
            f"（上游 {detail}）",
            code=ErrorCode.INTERNAL_ERROR,
            http_status=503,
        )


class UpstreamCodeError(WeatherUpstreamError):
    """上游用**响应体里的业务错误码**表达失败（旧信封 `{"code": "404"}`）。

    单独成类是为了让调用方能按 `upstream_code` 分支（如 404 → 城市不存在），
    而不是去 `str(exc)` 里搜 "404" 这种一改消息就失效的脆弱写法。
    """

    def __init__(self, upstream_code: str, message: str = "") -> None:
        self.upstream_code = upstream_code
        super().__init__(f"上游错误码 {upstream_code} {message}".strip())


def _upstream_detail(response: httpx.Response) -> str:
    """从上游错误响应里抽出可读描述。

    和风 v1 的错误体已改为 **RFC 7807（`application/problem+json`）**：

        {"error": {"status": 401, "type": "...#unauthorized",
                   "title": "Unauthorized",
                   "detail": "Authentication failed, check your KEY/Token or Host."}}

    旧的 `{"code": "401", "message": "..."}` 信封也一并兼容，避免迁移期两种格式都出现时漏判。
    """
    try:
        payload = response.json()
    except ValueError:
        # 上游可能返回 HTML 错误页（如 Host 头被改坏时），此时至少要留下点线索
        text = (response.text or "").strip()
        return f"(非 JSON 响应) {text[:200]}" if text else "(无响应体)"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            parts = [str(v) for v in (_pick(error, "title"), _pick(error, "detail")) if v]
            if parts:
                return " | ".join(parts)
        code = _pick(payload, "code")
        if code is not None and str(code) != "200":
            message = _pick(payload, "message", "msg", default="")
            return f"错误码 {code} {message}".strip()
    return response.text[:200].strip() or "(空响应体)"


def _pick(raw: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """按优先级返回第一个「存在且非空」的键值（见文件顶部字段名说明）。"""
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return default


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    number = _to_float(value)
    return default if number is None else int(round(number))


def _to_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    # 容器值一律视为「取错了」。这不是洁癖：str({"text": "阴"}) 会得到一个非空字符串，
    # 于是「字段名猜错」会伪装成「解析成功」，进而绕过 _reject_all_none 的假成功防线。
    # 2026-10-12 实测时条件字段正是 {"condition": {"text": "阴"}}，这个洞真实存在过。
    if isinstance(value, (dict, list, tuple, set)):
        return None
    return str(value)


# ---------------------------------------------------------------------------
# 嵌套取值与单位换算（2026-10-12 按实测真实响应体建立）
#
# 和风 v1 与旧 v7 是两套完全不同的**数据形状**，不只是改字段名：
#   v7： {"now": {"temp": "22", "text": "阴", "humidity": "71", "windDir": "东南风"}}
#        —— 扁平、字符串、百分比、中文风向、日期是 fxDate
#   v1： {"condition": {"text": "阴"}, "temperature": {"value": 22.51, "unit": "°C"},
#          "humidity": 0.71, "wind": {"direction": {"degree": 114, "compass": "ese"}},
#          "precipitation": {"amount": {"value": 0, "unit": "mm"}}}
#        —— 嵌套、数值带单位对象、比例而非百分比、日期是 UTC 的 forecastStartTime
# 因此下面这几个助手不是「多此一举的抽象」，而是两套形状之间唯一的翻译层。
# ---------------------------------------------------------------------------
_CST = timezone(timedelta(hours=8))


def _dig(raw: Any, *path: str) -> Any:
    """按路径安全下钻：`_dig(raw, "temperature", "value")`。

    任何一层不是 dict 就返回 None，不会抛异常——上游换了形状时，
    我们要的是「取不到值 → 报错」，而不是 KeyError 把请求炸掉。
    """
    current = raw
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _local_dt(value: Any) -> datetime | None:
    """把和风的 UTC 时刻（如 '2026-10-11T16:00Z'）转成东八区 datetime。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 兜底：遇到不带时区的串时按 UTC 解释，好过静默当成服务器本地时间
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_CST)


def _local_date_str(value: Any) -> str | None:
    """'2026-10-11T16:00Z' → '2026-10-12'（东八区）。"""
    parsed = _local_dt(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _local_time_str(value: Any) -> str | None:
    """'2026-10-11T21:55Z' → '2026-10-12 05:55'（东八区）。"""
    parsed = _local_dt(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else None


def _ratio_to_pct(value: Any) -> int | None:
    """把 0–1 的比例转成百分比整数。

    实测依据（2026-10-12，8 城市 × 3 天）：`humidity` 与 `precipitation.probability`
    **恒为 0–1 比例**——湿度多年在 0.38–0.89（即 38%–89%），昆明降雨日
    `probability=0.7` 且 `amount=5.71mm`、重庆 `probability=0.55` 且 `amount=3.2mm`。
    若直接当百分数会得到「湿度 1%」这种荒谬结果。

    `> 1` 的值按「上游已改成百分数」处理，这样上游哪天换单位不会算成 7100%。
    """
    number = _to_float(value)
    if number is None:
        return None
    return int(round(number * 100)) if number <= 1 else int(round(number))


def _speed_to_kmh(value: Any, unit: Any) -> float | None:
    """风速换算成 km/h（`schemas/weather.py` 契约里写死的单位）。

    实测上游给的 `unit` 是 `'m/s'`（如 0.47），必须 ×3.6。
    刻意**读 unit 而不是写死 ×3.6**：上游若改成 km/h，写死会把 7.88 算成 28.4。
    """
    number = _to_float(value)
    if number is None:
        return None
    unit_text = str(unit or "").strip().lower()
    if unit_text in ("m/s", "mps"):
        return round(number * 3.6, 2)
    if unit_text in ("km/h", "kph", "kmh"):
        return round(number, 2)
    logger.warning("未知风速单位 %r，按 km/h 原样返回（值=%s）", unit, number)
    return round(number, 2)


# 十六方位中文名。实测与上游自带的 `compass` 英文缩写完全对得上
# （114° → 东南偏东 = ese；340° → 西北偏北 = nnw），两者互为交叉验证。
_WIND_DIR_CN = (
    "北", "东北偏北", "东北", "东北偏东",
    "东", "东南偏东", "东南", "东南偏南",
    "南", "西南偏南", "西南", "西南偏西",
    "西", "西北偏西", "西北", "西北偏北",
)


def _wind_direction(wind: Any) -> str | None:
    """从 `wind` 对象里取风向。

    上游同时给 `degree`（114）与 `compass`（'ese'）。用 degree 换算成中文十六方位——
    中文方位对模型与用户都更有语义（"ese" 没人看得懂），且能用上游的 compass 交叉验证。
    """
    if not isinstance(wind, dict):
        return None
    direction = wind.get("direction")
    if not isinstance(direction, dict):
        return None
    degree = _to_float(direction.get("degree"))
    if degree is not None:
        index = int(((degree % 360) + 11.25) // 22.5) % 16
        return _WIND_DIR_CN[index]
    return _to_str(direction.get("compass"))


def _match_date(
    daily: list[DailyForecast], date: str | None
) -> DailyForecast | None:
    if not date:
        return None
    wanted = date.strip()
    for item in daily:
        if item.date == wanted:
            return item
    return None


# ---------------------------------------------------------------------------
# 共享连接池
# ---------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """惰性创建并复用 AsyncClient。

    复用的意义：每次请求新建客户端会丢掉连接池与 TLS 会话，反复三次握手，
    对「一次查询要打 4 个接口」的场景损耗明显。
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.weather_timeout_seconds)
    return _client


async def close_client() -> None:
    """应用关闭时释放连接池（由 main.py 的 lifespan 调用）。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class QWeatherProvider:
    """和风天气实现。"""

    name = "qweather"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # 允许注入 client，便于测试用 MockTransport 替换真实网络
        self._client = client
        self._location_cache = TTLCache(default_ttl=settings.weather_cache_ttl_seconds)

    # -- 配置与请求 ------------------------------------------------------

    @property
    def base_url(self) -> str:
        host = settings.weather_api_host.strip().rstrip("/")
        if not host:
            raise WeatherNotConfiguredError()
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return host

    def _headers(self) -> dict[str, str]:
        api_key = settings.weather_api_key.strip()
        if not api_key:
            raise WeatherNotConfiguredError()
        # 密钥放请求头而不是 key= 查询参数：查询参数会进入访问日志、浏览器历史与 Referer
        return {"X-QW-Api-Key": api_key}

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发起一次 GET，带重试。

        重试只针对「网络异常」与「HTTP 5xx」；4xx 不重试——那是请求本身的问题
        （key 无效、路径写错、参数错），重试只会白白消耗配额。
        """
        client = self._client or _get_client()
        url = f"{self.base_url}{path}"
        headers = self._headers()
        attempts = max(1, settings.weather_max_retries + 1)
        last_detail = "未知错误"

        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_detail = f"网络异常 {type(exc).__name__}"
                logger.warning("天气接口网络异常（第 %s/%s 次）: %s", attempt, attempts, exc)
            else:
                status = response.status_code
                if status >= 500:
                    last_detail = f"上游 HTTP {status}"
                    logger.warning(
                        "天气接口上游 5xx（第 %s/%s 次）: %s", attempt, attempts, status
                    )
                elif status >= 400:
                    detail = f"HTTP {status}: {_upstream_detail(response)}"
                    # 401/403 是「配置错了」不是「上游暂时不可用」：重试无用，
                    # 且须让用户知道去改哪个环境变量（见 WeatherAuthError 的说明）。
                    if status in (401, 403):
                        raise WeatherAuthError(detail)
                    raise WeatherUpstreamError(detail)
                else:
                    return self._parse_payload(response)

            if attempt < attempts:
                # 指数退避：0.5s → 1s → 2s，避免上游抖动时被连续冲击
                await _sleep(0.5 * (2 ** (attempt - 1)))

        raise WeatherUpstreamError(last_detail)

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        """把「HTTP 2xx 的响应」判定为成功载荷或错误。

        关键点：**不能只看 HTTP 状态码**。和风 v1 用 RFC 7807，错误体自带 status 字段，
        而 body 里的状态与 HTTP 状态在代理/网关场景下并不保证一致。若只看状态码，
        「HTTP 200 + error 体」会被当成成功，于是 current/daily 全是 None 地"假装成功"——
        这正是 CLAUDE.md §8.3 / §11 明令禁止的失败方式。
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeatherUpstreamError("上游返回的不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise WeatherUpstreamError("上游返回结构异常")

        error = payload.get("error")
        if isinstance(error, dict):
            detail = _upstream_detail(response)
            upstream_status = _to_int(_pick(error, "status"), response.status_code)
            if upstream_status in (401, 403):
                raise WeatherAuthError(detail)
            raise WeatherUpstreamError(f"上游 {upstream_status}: {detail}")

        # 兼容旧信封：{"code": "200", ...}；没有 code 字段的响应视为成功
        code = _pick(payload, "code")
        if code is not None and str(code) != "200":
            raise UpstreamCodeError(str(code), str(_pick(payload, "message", "msg", default="")))
        return payload

    # -- 三个数据块 ------------------------------------------------------

    async def resolve_city(self, city: str) -> ResolvedLocation:
        cache_key = f"geo:{city}"
        cached: ResolvedLocation | None = self._location_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            payload = await self._request(
                GEO_CITY_LOOKUP_PATH,
                params={"location": city, "lang": "zh", "number": 1},
            )
        except UpstreamCodeError as exc:
            # 上游用 204/404 表示「没这个地名」，翻译成对用户有意义的错误。
            # 按 upstream_code 属性判断，不去 str(exc) 里搜数字——那写法一改消息就悄悄失效。
            if exc.upstream_code in ("204", "404"):
                raise CityNotFoundError(city) from exc
            raise

        raw_list = _pick(payload, "location", "locations", default=[])
        if not isinstance(raw_list, list) or not raw_list:
            raise CityNotFoundError(city)

        raw = raw_list[0]
        lat = _to_float(_pick(raw, "lat", "latitude"))
        lon = _to_float(_pick(raw, "lon", "longitude"))
        if lat is None or lon is None:
            raise WeatherUpstreamError("城市搜索返回中缺少经纬度，无法继续查询天气")

        resolved = ResolvedLocation(
            name=str(_pick(raw, "name", default=city)),
            lat=lat,
            lon=lon,
            location_id=_to_str(_pick(raw, "id", "locationId")),
            adm1=_to_str(_pick(raw, "adm1")),
            adm2=_to_str(_pick(raw, "adm2")),
            country=_to_str(_pick(raw, "country")),
        )
        self._location_cache.set(cache_key, resolved)
        return resolved

    async def fetch_current(self, location: ResolvedLocation) -> CurrentWeather | None:
        """实时天气（2026-10-12 按实测真实响应体校准）。

        真实形状是**扁平顶层**，没有 v7 的 `now` 包装，带单位的量是嵌套对象：
            {"condition": {"text": "阴", "code": "104"},
             "temperature": {"value": 22.51, "unit": "°C"},
             "feelsLike":   {"value": 24.25, "unit": "°C"},
             "humidity": 0.71,
             "wind": {"direction": {"degree": 114, "compass": "ese"},
                      "speed": {"value": 0.47, "unit": "m/s"}, "scale": 0},
             "precipitation": {"amount": {"value": 0, "unit": "mm"},
                               "intensity": {...}, "type": "none"},
             "pressure": {"value": 1017.98, "unit": "hPa"},
             "uvIndex": 4}
        """
        payload = await self._request(
            CURRENT_PATH.format(lat=location.lat, lon=location.lon),
            params={"lang": "zh"},
        )
        # v1 是扁平的；仍保留 v7 的 {"now": {...}} 包装兼容（取到的 raw 语义相同）
        raw = _pick(payload, *_CURRENT_OBJECT_KEYS)
        if not isinstance(raw, dict):
            raw = payload

        current = CurrentWeather(
            # v1 实测**没有**观测时间字段（`metadata.tag` 是数据版本标记，不是时刻）。
            # 与其编一个"看起来对"的时间，不如诚实地留空。
            observed_at=None,
            temperature=_to_float(_dig(raw, "temperature", "value")),
            feels_like=_to_float(_dig(raw, "feelsLike", "value")),
            condition=_to_str(_dig(raw, "condition", "text")),
            humidity=_ratio_to_pct(raw.get("humidity")),
            wind_direction=_wind_direction(raw.get("wind")),
            wind_speed=_speed_to_kmh(
                _dig(raw, "wind", "speed", "value"), _dig(raw, "wind", "speed", "unit")
            ),
            precipitation=_to_float(_dig(raw, "precipitation", "amount", "value")),
            pressure=_to_float(_dig(raw, "pressure", "value")),
            uv_index=_to_int(raw.get("uvIndex")),
        )
        self._reject_all_none(current, payload)
        return current

    @staticmethod
    def _reject_all_none(current: CurrentWeather, payload: dict[str, Any]) -> None:
        """一个字段都没认出来 → 报错，而不是返回全 None 的对象。

        返回全 None 是「假装成功」：上层会以为"天气就是这样"，用户拿到一句
        「今天气温 None 度」。这道防线在 2026-10-12 的实测中**差点被绕过**——
        当时 `condition` 命中了一个 dict，`str(dict)` 产生非空字符串让 all() 判假。
        现在 `_to_str` 已拒绝容器类型，防线才真正生效。
        """
        if all(value is None for value in current.model_dump().values()):
            raise WeatherUpstreamError(
                "实时天气返回中未识别出任何字段，字段名可能与预期不符"
                f"（顶层键: {sorted(payload.keys())}）"
            )

    async def fetch_daily(self, location: ResolvedLocation) -> list[DailyForecast]:
        """每日预报（2026-10-12 按实测真实响应体校准）。

        真实形状（7 天）：
            {"days": [{"forecastStartTime": "2026-10-11T16:00Z",
                       "forecastEndTime":   "2026-10-12T16:00Z",
                       "astro": {"sunrise": "2026-10-11T21:55Z",
                                 "sunset":  "2026-10-12T09:28Z", ...},
                       "temperatureMax": {"value": 25.07, "unit": "°C"},
                       "temperatureMin": {"value": 17.09, "unit": "°C"},
                       "uvIndexMax": 5,
                       "daytime":   {"condition": {"text": "多云"},
                                     "humidity": 0.7,
                                     "wind": {"direction": {...}, "speed": {...}},
                                     "precipitation": {"amount": {"value": 0},
                                                       "probability": 0}},
                       "nighttime": {...}}]}

        ⚠️ 日期是 **UTC 的 forecastStartTime**（16:00Z 相当于北京时间次日 00:00），
        必须先转东八区再取日期，否则整条预报会集体差一天——问「明天」会答成今天。
        """
        payload = await self._request(
            DAILY_PATH.format(lat=location.lat, lon=location.lon),
            params={"lang": "zh"},
        )
        raw_list = _pick(payload, *_DAILY_LIST_KEYS)
        if raw_list is None:
            # 「键不存在」与「键存在但是空列表」是两回事：前者说明我们猜错了字段名，
            # 必须报错（否则用户问「明天要带伞吗」会得到一片空白却不知道是代码的问题）。
            raise WeatherUpstreamError(
                "每日预报返回中未找到预报列表，字段名可能与预期不符"
                f"（顶层键: {sorted(payload.keys())}）"
            )
        if not isinstance(raw_list, list):
            return []

        forecasts: list[DailyForecast] = []
        skipped_no_date = 0
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            date = _local_date_str(raw.get("forecastStartTime"))
            if not date:
                # 兼容旧版本可能的日期键（v7 用 fxDate）
                date = _to_str(_pick(raw, "fxDate", "date", "forecastDate"))
            if not date:
                # 没有日期就无法与提问中的「明天」对应，跳过而不是塞一个假日期
                skipped_no_date += 1
                continue

            day = raw.get("daytime") if isinstance(raw.get("daytime"), dict) else {}
            precip = day.get("precipitation") if isinstance(day.get("precipitation"), dict) else {}

            forecasts.append(
                DailyForecast(
                    date=date,
                    temp_min=_to_float(_dig(raw, "temperatureMin", "value")),
                    temp_max=_to_float(_dig(raw, "temperatureMax", "value")),
                    condition_day=_to_str(_dig(raw, "daytime", "condition", "text")),
                    condition_night=_to_str(_dig(raw, "nighttime", "condition", "text")),
                    precip_probability=_ratio_to_pct(precip.get("probability")),
                    precipitation=_to_float(_dig(day, "precipitation", "amount", "value")),
                    humidity=_ratio_to_pct(day.get("humidity")),
                    uv_index=_to_int(raw.get("uvIndexMax")),
                    sunrise=_local_time_str(_dig(raw, "astro", "sunrise")),
                    sunset=_local_time_str(_dig(raw, "astro", "sunset")),
                    wind_direction_day=_wind_direction(day.get("wind")),
                    wind_speed_day=_speed_to_kmh(
                        _dig(day, "wind", "speed", "value"),
                        _dig(day, "wind", "speed", "unit"),
                    ),
                )
            )

        # 「列表拿到了但一条都没解析出来」是另一种假成功：字段名全错时会静默返回空预报，
        # 于是「明天要带伞吗」得到空白却无人察觉。这里把它变成响亮失败。
        if raw_list and not forecasts and skipped_no_date == len(raw_list):
            first = raw_list[0] if isinstance(raw_list[0], dict) else {}
            raise WeatherUpstreamError(
                "每日预报每条都未能解析出日期，字段名可能与预期不符"
                f"（首条键: {sorted(first.keys())}）"
            )
        return forecasts

    async def fetch_indices(self, location: ResolvedLocation) -> list[LifeIndex]:
        """生活指数（2026-10-12 按实测真实响应体校准）。

        实测要点：
        1. `type` 是**必填参数**，缺了直接 400 Missing Parameter（invalidParams: ["location","type"]）。
           `type=0` 表示「全部类型」，一次返回 16 类 × 3 天 = 48 条。
        2. `location` 用经纬度 `"经度,纬度"` 有效（与和风文档一致），不必先拿 LocationID——
           这一点很重要：城市搜索接口当前不可用时，生活指数仍能独立工作。
        3. 响应是**旧信封** `{"code": "200", "updateTime": ..., "daily": [...]}`，
           不是 v1 的 RFC 7807，所以 `_parse_payload` 的旧信封兼容分支在这里生效。
           条目字段：date / type / name / level / category / text。
        """
        payload = await self._request(
            INDICES_PATH,
            params={
                "location": f"{location.lon},{location.lat}",
                "type": 0,
                "lang": "zh",
            },
        )
        raw_list = _pick(payload, *_INDICES_LIST_KEYS)
        if raw_list is None:
            # 生活指数是「降级项」，不抛错；但要留下校准线索，
            # 否则字段名猜错时我们只会看到「生活指数永远是空的」而无从下手。
            logger.warning(
                "生活指数返回中未找到列表键，已按空处理（顶层键: %s）", sorted(payload.keys())
            )
            return []
        if not isinstance(raw_list, list):
            return []
        indices: list[LifeIndex] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            index_type = _to_str(_pick(raw, "type", "indexType"))
            name = _to_str(_pick(raw, "name", "indexName"))
            if not index_type or not name:
                continue
            indices.append(
                LifeIndex(
                    type=index_type,
                    name=name,
                    level=_to_str(_pick(raw, "level")),
                    category=_to_str(_pick(raw, "category")),
                    text=_to_str(_pick(raw, "text")),
                )
            )
        return indices

    async def _fetch_indices_safe(self, location: ResolvedLocation) -> list[LifeIndex]:
        """生活指数失败不影响主流程（CLAUDE.md §1「外部 API 失败有降级」）。

        降级刻意做得宽：生活指数是附加信息，即使路径/参数日后变化，
        也不该把整个天气查询拖垮——返回空列表 + warning 更合适。
        """
        try:
            return await self.fetch_indices(location)
        except AppError as exc:
            logger.warning("生活指数获取失败，已降级为空: %s", exc)
            return []

    async def fetch_weather(self, city: str, date: str | None = None) -> WeatherResult:
        location = await self.resolve_city(city)
        # 三个数据块并行拉取：总耗时取决于最慢的一个，而不是三者之和
        current, daily, indices = await asyncio.gather(
            self.fetch_current(location),
            self.fetch_daily(location),
            self._fetch_indices_safe(location),
        )
        return WeatherResult(
            provider=self.name,
            query_city=city,
            resolved=location,
            current=current,
            daily=daily,
            indices=indices,
            target=_match_date(daily, date),
        )


# ---------------------------------------------------------------------------
# 对外门面
# ---------------------------------------------------------------------------
_weather_cache = TTLCache(default_ttl=settings.weather_cache_ttl_seconds)
_provider: QWeatherProvider | None = None


def get_provider() -> QWeatherProvider:
    """按 WEATHER_PROVIDER 返回供应商实例。

    将来接入第二家供应商时，在这里加一个分支即可，上层代码不动（CLAUDE.md §9）。
    """
    global _provider
    if _provider is None:
        if settings.weather_provider != "qweather":
            raise UnsupportedProviderError(settings.weather_provider)
        _provider = QWeatherProvider()
    return _provider


async def get_weather(city: str, date: str | None = None) -> WeatherResult:
    """天气查询统一入口。

    date 传 None 表示「不限日期」，返回实时 + 全部预报；传 'YYYY-MM-DD' 时
    result.target 会指向命中的那一天（命中不到则为 None）。
    """
    normalised_city = (city or "").strip()
    if not normalised_city:
        raise WeatherError(
            "城市名不能为空", code=ErrorCode.PARAM_ERROR, http_status=400
        )

    cache_key = f"weather:{settings.weather_provider}:{normalised_city}:{date or '-'}"
    cached: WeatherResult | None = _weather_cache.get(cache_key)
    if cached is not None:
        # deep=True：连嵌套的 current / daily / indices 也一起拷贝，
        # 否则调用方改 result.current.temp_c 仍会写到缓存里的同一批子对象。
        metrics_module.inc_counter("weather_cache_hits_total", "天气缓存命中次数")
        return cached.model_copy(deep=True, update={"cached": True})

    metrics_module.inc_counter("weather_calls_total", "天气外部调用次数")
    result = await get_provider().fetch_weather(normalised_city, date)
    # 缓存存副本、返回值归调用方所有。两条分支都必须「谁改都互不影响」，
    # 否则缓存会被就地污染（曾经因此挂过一次测试）。
    _weather_cache.set(cache_key, result.model_copy(deep=True, update={"cached": False}))
    return result


def cache_stats() -> dict[str, int]:
    """缓存命中统计，用于观察省下多少次外部调用（免费版有每日配额）。"""
    return _weather_cache.stats()


def clear_cache() -> None:
    _weather_cache.clear()
