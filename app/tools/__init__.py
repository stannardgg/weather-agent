"""【L3 能力层】Agent 可调用的工具。

M2 已落地：`base.py` 供应商协议、`weather.py` 和风天气实现、`cache.py` 进程内 TTL 缓存。
对外入口是 `weather.get_weather(city, date)` —— 上层只调这一个函数（CLAUDE.md §9）。
"""

