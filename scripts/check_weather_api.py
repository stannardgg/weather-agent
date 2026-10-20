"""M2 天气接口校准脚本：核对端点路径与响应字段。

存在的理由：和风已迁移到 v1 + 经纬度架构，其**数据形状与旧 v7 完全不同**
（嵌套 / 带单位对象 / 0–1 比例 / UTC 时刻），而部分路径与字段名无法从公开文档确认。
本脚本把 4 个接口的原始 HTTP 返回全部打印出来，一次运行即可定位需要修正的常量。

两种用法：

1) 只验证链路（无真实 key 也能跑）——看请求是否发得出去、上游怎么回、错误怎么映射：
       .venv/Scripts/python.exe scripts/check_weather_api.py 上海

2) 真校准（填好真实 Host + KEY 之后）——对照打印出的真实响应体核对
   app/tools/weather.py 顶部的路径常量与解析层。

⚠️ 脚本刻意**不因单步失败而中止**：某条路径被拒（如 GeoAPI 的 403）时，
若提前退出就看不到另外三条的真实状态了。

📌 2026-10-12 用真实凭据跑出的结论（已回填进 app/tools/weather.py）：
   · current / daily → 200，字段名已逐项校准
   · /v7/indices/3d  → 200，但 **type 是必填参数**（缺了 400）
   · /geo/v2/city/lookup → 403 Security Restriction（凭据权限问题，非代码问题）
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

# 直接 `python scripts/xxx.py` 运行时，sys.path[0] 是 scripts/ 而不是项目根，
# 因此 app 包不可见。把项目根插到最前面，脚本才能脱离 pytest 独立运行。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.tools.weather import (  # noqa: E402
    CURRENT_PATH,
    DAILY_PATH,
    GEO_CITY_LOOKUP_PATH,
    INDICES_PATH,
    QWeatherProvider,
    ResolvedLocation,
)

CITY = sys.argv[1] if len(sys.argv) > 1 else "上海"

# 与 4 个接口一一对应的探测清单（路径 + 参数），与 provider 内部的调用方式保持一致。
# 经纬度用上海坐标写死：这一步只关心「路径是否存在、怎么被拒绝」，不关心地点对不对。
_LAT, _LON = 31.23171, 121.47264
PROBES = [
    ("1/4 城市搜索", GEO_CITY_LOOKUP_PATH, {"location": CITY, "lang": "zh", "number": 1}),
    ("2/4 实时天气", CURRENT_PATH, {"lang": "zh"}),
    ("3/4 每日预报", DAILY_PATH, {"lang": "zh"}),
    # 实测：type 是**必填**参数，缺了会 400 Missing Parameter；type=0 返回全部类型。
    # 经纬度用 "经度,纬度"（与 /weather/v1/* 走路径参数不同，这里是查询参数）。
    ("4/4 生活指数", INDICES_PATH, {"location": f"{_LON},{_LAT}", "type": 0, "lang": "zh"}),
]


def _dump(title: str, obj: object) -> None:
    print(f"    {title}: {json.dumps(obj, ensure_ascii=False, default=str)[:1200]}")


def _looks_like_placeholder(key: str) -> bool:
    """识别 .env 里的占位假 key（连续数字或明显的示例串）。"""
    return key.startswith("0123456789") or key in {"test-key", "your-key", "changeme"}


async def _raw_probe(provider: QWeatherProvider) -> dict[str, dict]:
    """绕过解析层，直接把 4 个路径的原始 HTTP 结果打出来。

    这是校准的核心：解析层会把错误"翻译"掉，而校准需要的恰恰是未翻译的原始状态码与响应体。
    """
    results: dict[str, dict] = {}
    client = httpx.AsyncClient(timeout=settings.weather_timeout_seconds)
    base = provider.base_url
    headers = provider._headers()

    print(f"\n=== 原始 HTTP 探测（base = {base}）===")
    for label, path, params in PROBES:
        # 路径里的 {lat}/{lon} 用固定坐标填上，只为把请求发出去
        if "{lat}" in path:
            path = path.format(lat=_LAT, lon=_LON)
        url = f"{base}{path}"
        try:
            response = await client.get(url, params=params, headers=headers)
            body: object
            try:
                body = response.json()
            except ValueError:
                body = (response.text or "").strip()[:200] or "(空响应体)"
            has_error = isinstance(body, dict) and "error" in body
            verdict = "⚠️ 错误体" if has_error else "✅ 非错误体"
            print(f"\n{label}  {path}")
            print(f"    HTTP {response.status_code} | content-type={response.headers.get('content-type', '-')}")
            print(f"    判读: {verdict}")
            _dump("原始返回", body)
            results[label] = {"status": response.status_code, "body": body}
        except Exception as exc:  # noqa: BLE001 —— 校准脚本要尽量把每种失败都展示出来
            print(f"\n{label}  {path}")
            print(f"    [异常] {type(exc).__name__}: {exc}")
            results[label] = {"status": None, "body": str(exc)}

    await client.aclose()
    return results


async def _normalisation_probe(provider: QWeatherProvider) -> None:
    """跑一遍解析层，看归一化结果对不对。

    城市解析失败（例如 GeoAPI 被上游安全策略拒绝）时**不中止**，改用固定坐标
    继续验证另外三个接口——「城市搜索不可用」不该让我们失去校准其余接口的能力。
    """
    print("\n=== 解析层（归一化）探测 ===")
    try:
        resolved = await provider.resolve_city(CITY)
        _dump("城市解析结果", resolved.model_dump())
    except Exception as exc:  # noqa: BLE001
        print(f"  [城市解析失败] {type(exc).__name__}: {exc}")
        print("  → 改用固定坐标继续验证实时天气 / 每日预报 / 生活指数的归一化结果。")

    location = ResolvedLocation(name=CITY, lat=_LAT, lon=_LON)
    for label, coro in (
        ("实时天气", provider.fetch_current(location)),
        ("每日预报", provider.fetch_daily(location)),
        ("生活指数", provider.fetch_indices(location)),
    ):
        try:
            value = await coro
            if isinstance(value, list):
                _dump(label, [v.model_dump() for v in value[:2]])
            else:
                _dump(label, value.model_dump() if value else None)
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] {label}: {type(exc).__name__}: {exc}")


def _summarise(results: dict[str, dict]) -> None:
    print("\n=== 汇总 ===")
    statuses = {label: info.get("status") for label, info in results.items()}
    print(f"各步 HTTP 状态：{json.dumps(statuses, ensure_ascii=False)}")

    city_status = statuses.get("1/4 城市搜索")
    if all(status is None for status in statuses.values()):
        print("→ 全部是网络异常：检查网络/代理，或 WEATHER_API_HOST 是否写错域名。")
    elif city_status == 404:
        print("→ 城市搜索路径 404：需要修正 app/tools/weather.py 的 GEO_CITY_LOOKUP_PATH。")
    elif city_status == 401:
        print("→ 401：KEY 无效或尚未生效（新凭据可能需要几分钟同步到各节点）。")
    elif city_status == 403:
        print("→ 城市搜索 403。实测有两种 403，看响应体 error.type 区分：")
        print("   · #invalid-host          → Host 不是项目专属 Host（共享域名会被拒）")
        print("   · #security-restriction  → 凭据的「API 限制 / 应用限制」，或 GeoAPI 权限未生效")
        print("   后者请到控制台「项目管理 → 选中项目与凭据」核对，并在改动后等权限生效")
        print("   （官方称安全设置最长 1–2 小时生效；新建凭据通常更快）。")
    else:
        print("→ 有非错误响应，请对照上面的原始返回体核对字段名。")


async def main() -> int:
    print(f"provider   = {settings.weather_provider}")
    print(f"api_host   = {settings.weather_api_host or '(未配置)'}")
    print(f"api_key    = {'已配置' if settings.weather_api_key else '(未配置)'}")
    print(f"查询城市   = {CITY}")
    print("当前使用的路径常量（对照下面各步的返回判断是否需要修正）：")
    for label, path in (
        ("城市搜索", GEO_CITY_LOOKUP_PATH),
        ("实时天气", CURRENT_PATH),
        ("每日预报", DAILY_PATH),
        ("生活指数", INDICES_PATH),
    ):
        print(f"  {label}: {path}")

    if not settings.weather_api_host or not settings.weather_api_key:
        print("\n[中止] 请先在 .env 中配置 WEATHER_API_HOST 与 WEATHER_API_KEY")
        return 2

    if _looks_like_placeholder(settings.weather_api_key):
        print("\n[提示] 检测到占位假 key。本次运行只能验证「网络链路 + 错误映射」，")
        print("       拿不到成功响应，因此字段名仍然无法校准。")

    provider = QWeatherProvider()
    results = await _raw_probe(provider)
    await _normalisation_probe(provider)
    _summarise(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
