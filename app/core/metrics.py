"""【L1】进程内指标（M6-Q3 方案 A）。

零依赖：用内存字典存「计数」与「求和」，渲染成 Prometheus 文本格式，任何监控系统
（Prometheus / Grafana Agent / 脚本）都能抓取。

## 为什么自写而不是 prometheus-client

单机部署下，官方客户端的「直方图分位、进程内注册表、多进程聚合」这些能力用不太上，
而「多一个依赖」按 §2.3 是需要理由的。这里只做两种最常用的指标类型（counter / summary），
覆盖 §1「可观测」要求的全部数字：请求量、延迟、LLM 调用次数、token 消耗、检索耗时。

## 两种指标类型

- **counter**（只增不减）：请求数、调用次数、token 消耗。
- **summary**（求和 + 次数）：记录每次观测值，渲染出 `_sum`（总和）与 `_count`（次数），
  监控端用「sum / count」即可算出平均延迟。

## 线程安全

与 `rate_limit.py` 同理：只在事件循环内被调用，`dict` 的读写是原子的，无需加锁。
"""

from __future__ import annotations

from collections import defaultdict

# 指标注册表：name -> {"type": "counter"|"summary", "help": ...}
# 用 dict 而非 list 是为了 setdefault 幂等（同一指标多处埋点也只注册一次）。
_REGISTRY: dict[str, dict[str, str]] = {}

# 时间序列值：完整 series 标识（"name{labels}"）-> 数值
_SERIES: dict[str, float] = defaultdict(float)


def _label_str(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


def _fmt(value: float) -> str:
    """整数去掉小数点，浮点保留原样。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value)


def inc_counter(
    name: str, help_: str, *, value: int = 1, labels: dict[str, str] | None = None
) -> None:
    """计数器 +value。"""
    _REGISTRY.setdefault(name, {"type": "counter", "help": help_})
    _SERIES[name + _label_str(labels)] += value


def observe_summary(
    name: str, help_: str, value: float, labels: dict[str, str] | None = None
) -> None:
    """记录一次观测：累加到 `_sum`，`_count` 加一。"""
    _REGISTRY.setdefault(name, {"type": "summary", "help": help_})
    suffix = _label_str(labels)
    _SERIES[name + "_sum" + suffix] += value
    _SERIES[name + "_count" + suffix] += 1


def render() -> str:
    """渲染成 Prometheus 文本格式。"""
    lines: list[str] = []
    for name in sorted(_REGISTRY):
        meta = _REGISTRY[name]
        lines.append(f"# HELP {name} {meta['help']}")
        lines.append(f"# TYPE {name} {meta['type']}")
        if meta["type"] == "counter":
            # series key 是 name 或 name{...}——用「name 或 name{」精确匹配，避免前缀误伤
            for key, value in sorted(_SERIES.items()):
                if key == name or key.startswith(name + "{"):
                    lines.append(f"{key} {_fmt(value)}")
        else:  # summary：series key 是 name_sum / name_count（可带 label）
            prefix = name + "_"
            for key, value in sorted(_SERIES.items()):
                if key.startswith(prefix):
                    lines.append(f"{key} {_fmt(value)}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset() -> None:
    """清空全部指标（供测试隔离）。"""
    _REGISTRY.clear()
    _SERIES.clear()
