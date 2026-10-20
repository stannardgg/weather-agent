"""Agent 评测脚本：用金标集量化「路由、护栏、工具选择」三件事。

## 为什么需要它

在它出现之前，这套系统的质量判断只能靠"跑几个问题看看答得像不像话"。
那种验证有三个问题：不可重复（每次问题不一样）、不可比较（改完不知道变好还是变坏）、
不可交付（没法回答"准确率多少"）。评测集把"感觉"换成"数字"。

## 两种模式

- `--mode rules`（默认，**完全离线**）：只评规则层——意图分类（`router.classify_by_rules`）
  与输入护栏（`guardrail.is_blocked`）。不联网、不消耗额度、结果确定，可以进 CI。
- `--mode full`（需要 `.env` 里配好 LLM）：真实模型跑完整图，额外评**工具选择准确率**
  与端到端短路行为。这是唯一能验证"模型选得对不对"的方式。

## 它证明什么，不证明什么

`rules` 模式的意图准确率**不代表线上效果**：线上未命中规则时会交给 LLM 分类。
它衡量的是"规则覆盖了多少"，用来判断是否需要扩充关键词，而不是"系统有多准"。

`full` 模式评的是工具选择与短路，**仍不评答案的事实正确性**——那需要人工评分标准，
属于另一个层级的工作。本脚本刻意不做"LLM 当裁判"那套自评：让模型评自己的输出，
结论的可信度取决于评的那次调用，在没校准前不该写进报告当成指标。

## 用法

    python scripts/eval_agent.py                      # 规则层，默认
    python scripts/eval_agent.py --mode full          # 真实模型，评工具选择
    python scripts/eval_agent.py --json out.json      # 额外落一份机器可读报告

退出码：全部阈值通过返回 0，否则返回 1（可作 CI 门禁）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# 允许 `python scripts/eval_agent.py` 直接运行（不必先 pip install -e .）
sys.path.insert(0, str(ROOT))

CASES_PATH = ROOT / "evals" / "agent_cases.json"


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _ratio(hit: int, total: int) -> float:
    return hit / total if total else 0.0


def eval_rules(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """规则层评测：护栏准确率 + 规则命中的意图准确率。"""
    from app.agent import guardrail, router

    guard_hit = 0
    intent_hit = 0
    intent_judged = 0
    rows: list[dict[str, Any]] = []

    for case in cases:
        expected_blocked = bool(case["blocked"])
        blocked = guardrail.is_blocked(case["question"])
        guard_ok = blocked == expected_blocked
        guard_hit += int(guard_ok)

        row: dict[str, Any] = {
            "id": case["id"],
            "blocked_expected": expected_blocked,
            "blocked_actual": blocked,
            "guard_ok": guard_ok,
        }

        if not expected_blocked:
            verdict = router.classify_by_rules(case["question"])
            got = verdict[0].value if verdict else None
            row["intent_expected"] = case["intent"]
            row["intent_actual"] = got
            if got is not None:
                intent_judged += 1
                ok = got == case["intent"]
                intent_hit += int(ok)
                row["intent_ok"] = ok
            else:
                # 规则未命中：线上会交给 LLM 兜底，因此不计入规则准确率，
                # 但要单独计数——覆盖率的空白点就是这里。
                row["intent_ok"] = None
        rows.append(row)

    return {
        "mode": "rules",
        "total": len(cases),
        "guard_accuracy": _ratio(guard_hit, len(cases)),
        "intent_accuracy": _ratio(intent_hit, intent_judged),
        "rule_coverage": _ratio(intent_judged, len(cases)),
        "rows": rows,
    }


async def _run_full() -> dict[str, Any]:
    """真实模型端到端：评工具选择与短路。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401  确保 Base.metadata 里有表
    from app.agent import graph as graph_module
    from app.core.config import settings
    from app.db.base import Base
    from app.rag.embedder import reset_embedder
    from app.rag.vectorstore import reset_store

    if not settings.llm_configured:
        raise SystemExit(
            "full 模式需要真实模型：请在 .env 里配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"
        )

    cases = load_cases()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 向量库与上传目录都指向临时目录：评测不应该污染 data/ 下的真实库。
    # 向量化用占位实现：本脚本评的是"选没选对工具"，不是"检索质量"，
    # 用真实模型会让每次评测都多几十秒且依赖模型下载。
    with tempfile.TemporaryDirectory(prefix="eval-agent-") as tmp:
        settings.chroma_dir = str(Path(tmp) / "chroma")
        settings.chroma_collection = "eval"
        settings.upload_dir = str(Path(tmp) / "uploads")
        settings.embedding_provider = "placeholder"
        settings.embedding_dim = 64
        reset_store()
        reset_embedder()

        rows: list[dict[str, Any]] = []
        tool_hit = 0
        tool_judged = 0
        durations: list[float] = []
        tokens = 0

        async with session_factory() as db:
            for case in cases:
                if case["blocked"]:
                    # 被拦截的用例不跑图（模型根本不会被调用），护栏由 rules 模式评估
                    continue
                started = time.perf_counter()
                result = await graph_module.get_graph().ainvoke(
                    {
                        "question": case["question"],
                        "user_id": 1,
                        "db": db,
                        "history": [],
                        "notes": [],
                    }
                )
                durations.append(time.perf_counter() - started)
                tokens += int((result.get("usage") or {}).get("total_tokens") or 0)

                called = {item["tool"] for item in result.get("tool_trace") or []}
                expected = set(case.get("tools") or [])
                ok = called == expected
                tool_judged += 1
                tool_hit += int(ok)
                rows.append(
                    {
                        "id": case["id"],
                        "tools_expected": sorted(expected),
                        "tools_actual": sorted(called),
                        "tool_ok": ok,
                        "intent": result.get("intent"),
                        "short_circuit": bool(result.get("short_circuit")),
                        "answer_chars": len(result.get("answer") or ""),
                    }
                )

        reset_store()
        reset_embedder()

    await engine.dispose()
    return {
        "mode": "full",
        "total": tool_judged,
        "tool_accuracy": _ratio(tool_hit, tool_judged),
        "avg_latency_s": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "total_tokens": tokens,
        "rows": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"\n=== Agent 评测（mode={report['mode']}，样本 {report['total']} 条）===\n")
    if report["mode"] == "rules":
        print(f"护栏准确率      {report['guard_accuracy']:.1%}")
        print(f"规则意图准确率  {report['intent_accuracy']:.1%}（仅统计规则命中的样本）")
        print(f"规则覆盖率      {report['rule_coverage']:.1%}（未命中的会交给 LLM 兜底）")
    else:
        print(f"工具选择准确率  {report['tool_accuracy']:.1%}")
        print(f"平均耗时        {report['avg_latency_s']}s")
        print(f"总 token        {report['total_tokens']}")

    print("\n明细：")
    for row in report["rows"]:
        if report["mode"] == "rules":
            mark = "OK " if row["guard_ok"] else "FAIL"
            intent = row.get("intent_actual") or "未判出"
            print(f"  [{mark}] {row['id']:<6} 拦截={row['blocked_actual']!s:<5} 意图={intent}")
        else:
            mark = "OK " if row["tool_ok"] else "FAIL"
            print(
                f"  [{mark}] {row['id']:<6} 期望={row['tools_expected']} "
                f"实际={row['tools_actual']}"
            )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 评测（金标集）")
    parser.add_argument("--mode", choices=("rules", "full"), default="rules")
    parser.add_argument("--json", dest="json_path", default="", help="报告另存为 JSON")
    # 阈值默认宽松：rules 模式的意图准确率受关键词表限制，先观察再收紧。
    # 护栏没有理由低于 100%——它完全确定，误判就是规则写错了。
    parser.add_argument("--min-guard", type=float, default=1.0)
    parser.add_argument("--min-intent", type=float, default=0.0)
    parser.add_argument("--min-tool", type=float, default=0.7)
    args = parser.parse_args()

    report = eval_rules(load_cases()) if args.mode == "rules" else asyncio.run(_run_full())
    print_report(report)

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入 {args.json_path}")

    failed: list[str] = []
    if report["mode"] == "rules":
        if report["guard_accuracy"] < args.min_guard:
            failed.append(f"护栏准确率 {report['guard_accuracy']:.1%} < {args.min_guard:.0%}")
        if report["intent_accuracy"] < args.min_intent:
            failed.append(f"意图准确率 {report['intent_accuracy']:.1%} < {args.min_intent:.0%}")
    elif report["tool_accuracy"] < args.min_tool:
        failed.append(f"工具选择准确率 {report['tool_accuracy']:.1%} < {args.min_tool:.0%}")

    if failed:
        print("门禁未通过：")
        for item in failed:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
