"""真实大模型 API 体检。

**这是本项目唯一会真调线上服务、花真金白银的入口。** 测试套件永远走
`tests/fake_openai.py` 的本地假服务，不消耗额度；本脚本单独存在，就是为了
把「真实 API 到底通不通」这件事验证一次，并把证据打印出来。

它填掉 `docs/needs-extraction-test-record.md` 里那批「尚未验证」的空白：

| 待验证项 | 本脚本怎么验 |
| --- | --- |
| 真实 API 一次都没调过 | 真调，打印 provider / model / 耗时 |
| JSON 合规率 | `extract_json_object` 是否成功 + `validate_result` 问题列表 |
| evidence 粒度 | 逐条核对「模型给的证据是否逐字出现在原话里」 |
| 真实链路延迟 | 两次调用的墙钟耗时 |

## 用法

    python tools/check_llm.py
    python tools/check_llm.py --text "预算四千左右，拍照好一点，别太重"
    python tools/check_llm.py --json          # 额外打印完整契约 JSON
    python tools/check_llm.py --skip-recommend

配置走 `.env` / 环境变量，见 `.env.example`。

## 退出码

    0  全部检查通过
    1  有检查项失败（详情见输出）
    2  配置不全，连不上服务
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from bridge import bridge_contract  # noqa: E402
from engine import load_data, rank  # noqa: E402
from needs_extraction import extract_needs, validate_result  # noqa: E402
from providers.env import describe, load_dotenv  # noqa: E402
from providers.jev import JevUnavailable  # noqa: E402
from providers.recommend import Brief, fallback_text, write_recommendation  # noqa: E402
from schemas import CRITERIA  # noqa: E402

DEFAULT_TEXT = "预算三千左右，想要轻一点的，主要看价格，256G 起步就行"

CRITERION_LABELS = {
    "price": "价格更低",
    "weight": "机身更轻",
    "storage": "存储更大",
    "refresh": "刷新率更高",
    "screen": "屏幕接近偏好",
}

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def _utf8_stdout() -> None:
    """Windows 控制台默认 GBK，打印中文机型名会炸。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def rule(title: str) -> None:
    print()
    print("─" * 68)
    print(title)
    print("─" * 68)


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = PASS if ok else FAIL
    print(f"  {mark} {label}" + (f"　{detail}" if detail else ""))
    return ok


def _models_payload(result_frame, limit: int, budget_high: float) -> list[dict]:
    """与 app.py 的 `_models_payload` 保持一致，确保体检的就是真实链路。"""
    models = result_frame.drop_duplicates("产品名称").head(limit)
    payload = []
    for _, row in models.iterrows():
        payload.append(
            {
                "name": str(row["产品名称"]),
                "config": str(row["配置"]),
                "chip": str(row["处理器"]),
                "price": float(row.price),
                "weight": float(row.weight),
                "screen": float(row.screen),
                "refresh": float(row.refresh),
                "storage": float(row.storage),
                "score": float(row.score),
                "reason": f"比预算上限低 {budget_high - row.price:,.0f} 元",
            }
        )
    return payload


def _needs_summary(result: dict, bridged) -> str:
    """与 app.py 的 `_needs_summary` 同构。"""
    parts: list[str] = []
    if bridged.brands:
        parts.append("品牌只看：" + "、".join(bridged.brands))
    if bridged.excluded_brands:
        parts.append("排除品牌：" + "、".join(bridged.excluded_brands))
    stated = [
        f"{CRITERION_LABELS[item['criterion']]}={item['level']}"
        for item in result.get("preferences") or []
        if item.get("level") != "未提及"
    ]
    parts.append("偏好：" + ("，".join(stated) if stated else "用户没有表达任何偏好"))
    return "；".join(parts)


def section_extraction(text: str, timeout: float, dump_json: bool) -> tuple[bool, dict]:
    """第一段：需求抽取。返回 (是否全通过, 契约结果)。"""
    rule(f"① 需求抽取　输入：{text!r}")

    from providers.llm import LLMClassifier

    try:
        classifier = LLMClassifier(timeout=timeout)
    except JevUnavailable as error:
        print(f"  {FAIL} 构造分类器失败：{error}")
        return False, {}

    started = time.perf_counter()
    result = extract_needs(text, classifier=classifier)
    wall_ms = (time.perf_counter() - started) * 1000

    status = result.get("status")
    meta = result.get("meta") or {}
    ok = status in ("ok", "degraded")

    check(f"调用成功（status={status}）", ok, meta.get("error") or "")
    print(f"     服务商：{meta.get('provider') or '（未知）'}")
    print(f"     模型：{meta.get('model') or '（未知）'}")
    print(f"     耗时：链路内 {meta.get('elapsed_ms')} ms　墙钟 {wall_ms:.0f} ms")

    if not ok:
        return False, result

    # --- JSON 合规性 ---
    problems = validate_result(result)
    check(
        f"契约结构合规（validate_result 返回 {len(problems)} 个问题）",
        not problems,
    )
    for problem in problems:
        print(f"       · {problem}")

    # --- 抽取内容 ---
    budget = result["budget"]
    print()
    print("  【预算】")
    print(
        f"     reference={budget.get('reference')}　"
        f"min={budget.get('min')}　max={budget.get('max')}"
    )
    print(f"     证据：{budget.get('evidence') or '（无）'}")
    print(
        f"  【硬约束】最低存储 {result.get('min_storage')}　"
        f"目标屏幕 {result.get('target_screen')}　"
        f"白名单 {result.get('brands', {}).get('include')}　"
        f"排除 {result.get('brands', {}).get('exclude')}"
    )

    print("  【偏好】")
    for item in result["preferences"]:
        label = CRITERION_LABELS.get(item["criterion"], item["criterion"])
        print(f"     {label:<12} {item['level']:<5} 证据：{item.get('evidence') or '（无）'}")

    unsupported = result.get("unsupported_needs") or []
    print(f"  【覆盖不到的需求】{'、'.join(i['need'] for i in unsupported) or '（无）'}")
    for item in unsupported:
        print(f"     证据：{item.get('evidence') or '（无）'}")

    clarifications = result.get("clarifications") or []
    print(f"  【待确认】{len(clarifications)} 项")
    for item in clarifications:
        print(f"     · [{item['field']}] {item['question']}")

    # --- evidence 保真：这是「evidence 粒度」那一项的核心检查 ---
    print()
    fabricated: list[str] = []
    for item in result["preferences"]:
        evidence = (item.get("evidence") or "").strip()
        if evidence and evidence not in text:
            fabricated.append(f"{item['criterion']}: {evidence!r}")
    for item in unsupported:
        evidence = (item.get("evidence") or "").strip()
        if evidence and evidence not in text:
            fabricated.append(f"unsupported/{item['need']}: {evidence!r}")
    check(
        "evidence 逐字来自原话（无编造）",
        not fabricated,
        "；".join(fabricated) if fabricated else f"共核对 {len(result['preferences'])} 个维度",
    )

    # 含糊预算不得被封顶：本模块 validate_result 已查，这里再直白说一次
    if "左右" in text or "多" in text:
        check(
            "含糊预算没有被擅自封顶",
            budget.get("max") is None or budget.get("reference") is None,
            f"reference={budget.get('reference')}, max={budget.get('max')}",
        )

    if dump_json:
        print()
        print("  【完整契约 JSON】")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return True, result


def section_recommend(result: dict, timeout: float, top: int) -> bool:
    """第二段：推荐语。核对「推荐语只提清单内机型」这条防幻觉红线。"""
    rule("② 推荐语生成（喂真实引擎结果）")

    try:
        frame = load_data()
    except Exception as error:  # noqa: BLE001
        print(f"  {FAIL} 数据读取失败：{error}")
        return False

    bridged, degraded = bridge_contract(result, known_brands=sorted(frame.brand.unique()))
    if degraded:
        print(f"  {WARN}降级项：{'；'.join(degraded)}")
    print(
        f"  条件：预算 ¥{bridged.budget[0]:,}–¥{bridged.budget[1]:,}　"
        f"存储 ≥{bridged.storage}GB　屏幕目标 {bridged.target:g}\"　"
        f"白名单 {bridged.brands or '不限'}　排除 {bridged.excluded_brands or '无'}"
    )

    candidates = frame[
        frame.price.between(*bridged.budget) & (frame.storage >= bridged.storage)
    ]
    if bridged.brands:
        candidates = candidates[candidates.brand.isin(bridged.brands)]
    if bridged.excluded_brands:
        candidates = candidates[~candidates.brand.isin(bridged.excluded_brands)]

    if candidates.empty:
        print(f"  {WARN}当前条件无候选，跳过推荐语（这本身不是失败）")
        return True

    weights = bridged.weights
    if sum(weights.values()) <= 0:
        print(f"  {FAIL}权重全为 0，无法排序")
        return False

    result_frame = rank(candidates, weights, bridged.target)
    models = _models_payload(result_frame, top, bridged.budget[1])
    shown = {model["name"] for model in models}

    print(f"  候选配置 {len(candidates)} 条，取前 {len(models)} 款：")
    for index, model in enumerate(models, 1):
        print(
            f"     {index}. {model['name']}　{model['config']}　"
            f"¥{model['price']:,.0f}　匹配度 {model['score'] * 100:.1f}%"
        )

    brief = Brief(
        models=models,
        needs_summary=_needs_summary(result, bridged),
        pending=bridged.pending,
        unsupported=bridged.unsupported,
        candidate_configs=len(candidates),
        total_configs=len(frame),
        budget=bridged.budget,
    )

    started = time.perf_counter()
    advice, error = write_recommendation(brief, timeout=timeout)
    wall_ms = (time.perf_counter() - started) * 1000

    print()
    if error:
        print(f"  {FAIL}推荐语生成失败：{error}")
        print(f"     界面会改用模板文案（{len(fallback_text(brief))} 字），机型列表不受影响。")
        return False

    print(f"  {PASS}推荐语生成成功（墙钟 {wall_ms:.0f} ms，{len(advice)} 字）")
    print()
    for line in advice.splitlines():
        print(f"     {line}")

    # --- 防幻觉红线 ---
    print()
    known = set(frame["产品名称"].astype(str))
    mentioned = {name for name in known if name in advice}
    intruders = mentioned - shown
    check(
        "推荐语没有提到清单外的机型",
        not intruders,
        f"越界：{sorted(intruders)}" if intruders else f"提到 {sorted(mentioned)}",
    )
    check(
        "推荐语确实提到了清单内的机型",
        bool(mentioned & shown),
        f"命中 {len(mentioned & shown)}/{len(shown)} 款",
    )

    # 匹配度不得被说成质量分
    bad_words = [w for w in ("质量分", "质量评分", "购买概率", "最佳", "最强") if w in advice]
    check("推荐语没有把匹配度说成质量分/最佳", not bad_words, f"出现：{bad_words}" if bad_words else "")

    if bridged.unsupported:
        missed = [item for item in bridged.unsupported if item not in advice]
        check(
            "推荐语交代了覆盖不到的需求",
            not missed,
            f"漏说：{missed}" if missed else f"已提到 {'、'.join(bridged.unsupported)}",
        )

    return True


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="真实大模型 API 体检")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要抽取的用户需求原文")
    parser.add_argument("--top", type=int, default=5, help="推荐几款（默认 5）")
    parser.add_argument("--timeout", type=float, default=60.0, help="单次请求超时秒数")
    parser.add_argument("--json", action="store_true", help="打印完整契约 JSON")
    parser.add_argument("--skip-recommend", action="store_true", help="只测需求抽取")
    args = parser.parse_args()

    print("知选 · 真实大模型 API 体检")
    print("=" * 68)

    rule("⓪ 配置")
    report = load_dotenv()
    print(f"  {report.summary}")
    if report.problems:
        for problem in report.problems:
            print(f"  {WARN}{problem}")
    for key, value in describe():
        print(f"     {key:<14} {value}")
    from providers.llm import thinking_from_env

    thinking = thinking_from_env()
    print(
        f"     {'LLM_THINKING':<14} "
        + ("开（慢且 temperature 失效）" if thinking else "关（默认，推荐）")
    )

    from providers.llm import LLMClassifier

    try:
        probe = LLMClassifier(timeout=args.timeout)
    except JevUnavailable as error:
        print()
        print(f"  {FAIL}配置不全：{error}")
        print("     照着 .env.example 配好 .env，或用环境变量传进去，再跑一次。")
        return 2
    if not os.environ.get("LLM_API_KEY"):
        print()
        print(f"  {FAIL}未设置 LLM_API_KEY。密钥只放环境变量或 .env，不写进代码。")
        return 2
    print(f"  {PASS}配置齐全：{probe.model} @ {probe.base_url}")

    passed_extraction, result = section_extraction(args.text, args.timeout, args.json)

    passed_recommend = True
    if passed_extraction and not args.skip_recommend:
        passed_recommend = section_recommend(result, args.timeout, args.top)

    rule("汇总")
    all_ok = passed_extraction and passed_recommend
    print(f"  需求抽取：{'通过' if passed_extraction else '失败'}")
    if not args.skip_recommend:
        print(f"  推荐语：{'通过' if passed_recommend else '失败'}")
    print()
    print(f"  {PASS}全部通过，真实 API 已接通。" if all_ok else f"  {FAIL}有检查项未通过，见上方标记。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
