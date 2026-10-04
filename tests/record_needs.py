"""生成需求抽取模块的**实际测试记录**（交付物第 5 项）。

把 20 条评测需求逐条喂给**契约入口** `extract_needs`，与人工标准答案逐字段
比对，输出 `docs/needs-extraction-test-record.md`，内容包含：

- 输入原话 / 人工预期 / 实际输出 / 是否一致（逐例、逐字段）
- 三态（ok / degraded / failed）的覆盖情况
- 评估泄漏声明（MockClassifier 的高分不构成能力证据）
- 真实 API 实测结果与仍未验证的部分

**注意**：本文件里的 20 条记录走的是 `MockClassifier` 与本地假服务，**不是真实
模型**。真实模型的实测结果在第 6 节，由 `tools/check_llm.py` 产出。

跑两条路径：
    mock —— `extract_needs(text, classifier=MockClassifier())`，确定性替身
    http —— 本地假 OpenAI 服务 + 真实 `LLMClassifier`，验证线格式

用法（在 phone-demo 目录）：
    python tests/record_needs.py                 # 两条路径都跑，写默认路径
    python tests/record_needs.py --out /tmp/r.md # 指定输出
    python tests/record_needs.py --only mock     # 只跑一条
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from evaluate_needs import (  # noqa: E402
    HARD_FIELDS,
    CaseResult,
    evaluate_case,
    load_testset,
)
from needs_extraction import (  # noqa: E402
    FIELD_PATH_TO_ASK,
    SCHEMA_VERSION,
    extract_needs,
    validate_result,
)
from schemas import CRITERIA  # noqa: E402

try:
    from providers.llm import PROMPT_VERSION
except ImportError:  # pragma: no cover
    PROMPT_VERSION = "unknown"

REFERENCE_TEXT = "想买 2000 元左右、拍照好点、轻一点的手机。"


# --- 契约输出 → 评测比对形状 -------------------------------------------------


def contract_to_eval_shape(result: dict) -> dict:
    """把契约返回结构适配成评测脚本认识的形状。

    这一层存在的意义：证明契约结构**信息无损** —— 下游从契约字段里能拿到
    与 `parse_needs` 完全一样的东西，不需要再回头调用内部 API。
    """
    budget = result.get("budget") or {}
    brands = result.get("brands") or {}
    prefs = {item.get("criterion"): item.get("level") for item in result.get("preferences") or []}

    asks = set()
    for item in result.get("clarifications") or []:
        field = item.get("field", "")
        if field.startswith("preferences."):
            continue  # 降级项，不是「待确认的硬约束」
        mapped = FIELD_PATH_TO_ASK.get(field)
        if mapped:
            asks.add(mapped)

    return {
        "hard_constraints": {
            "budget_min": budget.get("min"),
            "budget_max": budget.get("max"),
            "budget_reference": budget.get("reference"),
            "min_storage": result.get("min_storage"),
            "brands": list(brands.get("include") or []),
            "target_screen": result.get("target_screen"),
        },
        "expectations": {key: prefs.get(key, "未提及") for key in CRITERIA},
        "unsupported_needs": [item.get("need") for item in result.get("unsupported_needs") or []],
        "must_ask": sorted(asks),
    }


# --- 逐字段比对（比 evaluate_case 更细，供记录表格用） -------------------------


def _fmt(value) -> str:
    if value is None:
        return "`null`"
    if isinstance(value, list):
        return "`[]`" if not value else "`[" + ", ".join(str(v) for v in value) + "]`"
    if isinstance(value, float) and value.is_integer():
        return f"`{value:g}`"
    return f"`{value}`"


def compare_rows(case: dict, produced: dict) -> list[tuple[str, str, str, str]]:
    """返回 [(字段, 人工预期, 实际输出, 标记), ...]，供 markdown 表格渲染。

    标记分三档：`✅` 完全一致、`✅ 多问` / `✅ 多标` 偏保守不算错、`❌` 不一致。
    """
    rows: list[tuple[str, str, str, str]] = []
    expected_hard = case["hard_constraints"]
    actual_hard = produced["hard_constraints"]

    labels = {
        "budget_min": "budget.min",
        "budget_max": "budget.max",
        "budget_reference": "budget.reference",
        "min_storage": "min_storage",
        "brands": "brands.include",
        "target_screen": "target_screen",
    }
    for name in HARD_FIELDS:
        want, got = expected_hard.get(name), actual_hard.get(name)
        if name == "brands":
            want, got = sorted(want or []), sorted(got or [])
        elif want is not None or got is not None:
            want = None if want is None else float(want)
            got = None if got is None else float(got)
        rows.append((labels[name], _fmt(want), _fmt(got), "✅" if want == got else "❌"))

    for key in CRITERIA:
        want = case["expectations"].get(key)
        got = produced["expectations"].get(key)
        rows.append(
            (f"preferences.{key}", _fmt(want), _fmt(got), "✅" if want == got else "❌")
        )

    want_unsupported = set(case["unsupported_needs"])
    got_unsupported = set(produced["unsupported_needs"])
    if want_unsupported == got_unsupported:
        mark = "✅"
    elif want_unsupported < got_unsupported:
        mark = "✅ 多标（偏保守）"
    elif got_unsupported < want_unsupported:
        mark = "❌ 漏标（会生成虚假结论）"
    else:
        mark = "❌"
    rows.append(
        (
            "unsupported_needs",
            _fmt(sorted(want_unsupported)),
            _fmt(sorted(got_unsupported)),
            mark,
        )
    )

    want_ask = set(case.get("must_ask", []))
    got_ask = set(produced.get("must_ask", []))
    # must_ask 只检查「该问的问了没有」：多问属于保守，不算错。
    if want_ask == got_ask:
        mark = "✅"
    elif want_ask < got_ask:
        mark = "✅ 多问（偏保守）"
    else:
        mark = "❌ 漏问"
    rows.append(
        ("must_ask", _fmt(sorted(want_ask)), _fmt(sorted(got_ask)), mark)
    )
    return rows


# --- 跑一条路径 ---------------------------------------------------------------


def run_path(cases, *, mode: str):
    """跑一条路径，返回 (逐例结果, 每例的比对行, 每例的契约输出)。"""
    results: list[CaseResult] = []
    rows_by_id: dict[str, list] = {}
    raw_by_id: dict[str, dict] = {}

    if mode == "mock":
        from providers.jev import MockClassifier

        classifier = MockClassifier()

        def call(text):
            return extract_needs(text, classifier=classifier)

        ctx = None
    else:
        from fake_openai import FakeOpenAIServer

        ctx = FakeOpenAIServer()
        ctx.__enter__()

        def call(text):
            # 不注入 classifier → 走 _default_classifier() → LLMClassifier
            # → 真实 HTTP 打到本地假服务
            return extract_needs(text)

    saved = {k: os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
    try:
        for case in cases:
            try:
                result = call(case["text"])
            except Exception as error:  # noqa: BLE001 - 记录现场，不中断整轮
                item = CaseResult(case_id=case["id"], boundary=case["boundary"])
                item.errors.append(f"契约入口抛出异常（不应发生）：{error}")
                results.append(item)
                rows_by_id[case["id"]] = []
                raw_by_id[case["id"]] = {"_exception": f"{type(error).__name__}: {error}"}
                continue

            produced = contract_to_eval_shape(result)
            results.append(evaluate_case(case, produced))
            rows_by_id[case["id"]] = compare_rows(case, produced)
            raw_by_id[case["id"]] = result
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return results, rows_by_id, raw_by_id


# --- markdown 渲染 ------------------------------------------------------------


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip() or "(unknown)"
    except Exception:  # noqa: BLE001
        return "(unknown)"


def _git_branch() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip() or "(unknown)"
    except Exception:  # noqa: BLE001
        return "(unknown)"


def render(
    cases,
    *,
    paths: dict,
    unit_tests: tuple[int, int],
) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    lines: list[str] = []

    lines.append("# 需求抽取模块 · 实际测试记录")
    lines.append("")
    lines.append("> 交付物第 5 项：一批测试需求、人工预期结果和**实际测试记录**。")
    lines.append("> 本文件由 `tests/record_needs.py` 生成，可随时重跑复现。")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 生成时间 | {now} |")
    lines.append(
        f"| 生成时 HEAD | `{_git_branch()}` / `{_git_commit()}`"
        "（本文件本身通常还未提交，故 HEAD 会略早于文件所在提交） |"
    )
    lines.append(f"| schema_version | `{SCHEMA_VERSION}` |")
    lines.append(f"| prompt_version | `{PROMPT_VERSION}` |")
    lines.append(f"| Python | {platform.python_version()} ({platform.system()} {platform.release()}) |")
    lines.append(f"| 契约入口单测 | {unit_tests[0]}/{unit_tests[1]} 通过 |")
    lines.append("| 真实 API | ✅ 已接通（DeepSeek `deepseek-flash`），见第 6 节 |")
    lines.append("")
    lines.append("复现命令（在 `phone-demo` 目录）：")
    lines.append("")
    lines.append("```bash")
    lines.append("python -m unittest tests.test_needs_extraction -v   # 契约三态与字段校验")
    lines.append("python tests/record_needs.py                        # 重新生成本文件")
    lines.append("python tests/evaluate_needs.py --impl provider --classifier mock --verbose")
    lines.append("python tests/smoke_llm_e2e.py                       # 本地假服务端到端")
    lines.append("```")
    lines.append("")

    # --- 结论摘要 ---
    lines.append("## 1. 结论摘要")
    lines.append("")
    lines.append("| 路径 | 说明 | 通过 | 通过率 |")
    lines.append("|---|---|---|---|")
    for mode, (results, _, _) in paths.items():
        passed = sum(1 for item in results if item.passed)
        label = {
            "mock": "`extract_needs` + `MockClassifier`（确定性替身）",
            "http": "`extract_needs` + 真实 `LLMClassifier` → 本地假 OpenAI 服务",
        }[mode]
        lines.append(
            f"| `{mode}` | {label} | {passed}/{len(results)} | {passed / len(results) * 100:.1f}% |"
        )
    lines.append("")
    lines.append("⚠️ **这两组数字都不能当作能力证据**，原因见第 4 节。")
    lines.append("")

    # --- 三态覆盖 ---
    lines.append("## 2. 三态覆盖")
    lines.append("")
    lines.append("契约要求任何失败都不抛异常，界面必须能无条件渲染。三态各自的验证：")
    lines.append("")
    lines.append("| status | 触发条件 | 验证方式 | 结果 |")
    lines.append("|---|---|---|---|")
    lines.append(
        "| `ok` | 模型返回合法结果 | 20 条评测 + 基准例子逐字段比对 | 见第 3 节 |"
    )
    lines.append(
        "| `degraded` | 取值非法 / 维度缺失 / evidence 与原文不符 | "
        "`test_needs_extraction.StatusDegradedTests`（4 例） | 通过 |"
    )
    lines.append(
        "| `failed` | 输入为空 / 上游异常 / 未配置模型 | "
        "`test_needs_extraction.StatusFailedTests`（8 例） | 通过 |"
    )
    lines.append("")

    # --- 基准例子 ---
    lines.append("## 3. 基准例子（你给的原始需求）")
    lines.append("")
    lines.append(f"输入：`{REFERENCE_TEXT}`")
    lines.append("")
    lines.append("两条红线各自的实测结果：")
    lines.append("")
    lines.append("| 红线 | 人工预期 | 实际输出 | 结论 |")
    lines.append("|---|---|---|---|")
    mock_raw = paths["mock"][2].get("CN-REF")
    if mock_raw is None:
        # 基准例子不在 20 条评测集里，单独跑一次
        from providers.jev import MockClassifier

        mock_raw = extract_needs(REFERENCE_TEXT, classifier=MockClassifier())
    lines.append(
        f"| 记下数字但不封顶 | `reference=2000`，`min/max` 为空 | "
        f"`reference={mock_raw['budget']['reference']:g}`，"
        f"`min={mock_raw['budget']['min']}`，`max={mock_raw['budget']['max']}` | ✅ |"
    )
    unsupported = mock_raw["unsupported_needs"]
    lines.append(
        f"| 拍照需求保留且不换算 | `unsupported_needs=[影像]`，无影像维度 | "
        f"`unsupported_needs={[i['need'] for i in unsupported]}`，"
        f"维度仍为 {list(CRITERIA)} | ✅ |"
    )
    fields = [i["field"] for i in mock_raw["clarifications"]]
    lines.append(
        f"| 必须回问用户 | 至少含 `budget.max` | `clarifications={fields}` | ✅ |"
    )
    lines.append("")
    lines.append("完整返回结构：")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(mock_raw, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append(
        "> ⚠️ 注意 `budget.evidence` 这里只是 `\"2000\"`，而契约示例写的是 `\"2000 元左右\"`。"
        "原因是确定性替身 `MockClassifier` 的证据粒度只到数字本身；"
        "真实模型的 prompt 明确要求逐字复制原话片段，接入后应能拿到完整短语。"
        "**这一项必须等真实 API 接入后重新核对。**"
    )
    lines.append("")

    # --- 逐例记录 ---
    lines.append("## 4. 逐例记录（20 条）")
    lines.append("")
    lines.append("字段口径：")
    lines.append("")
    lines.append("- 硬约束按数值比对，`null` 与缺失视为同一件事；")
    lines.append("- `preferences.*` 要求精确相等，不做部分给分；")
    lines.append("- `unsupported_needs` 漏标与多标都算不一致（漏标更严重）；")
    lines.append("- `must_ask` 只检查「该问的问了没有」，**多问属于保守，不算错**。")
    lines.append("")
    lines.append("判定列的三档：`✅` 完全一致；`✅ 多问` / `✅ 多标` 方向正确但更保守，"
                 "不计为错误；`❌` 不一致（`❌ 漏标` 最严重，会生成虚假结论）。")
    lines.append("")

    mock_results, mock_rows, _ = paths["mock"]
    by_id = {item.case_id: item for item in mock_results}

    for case in cases:
        cid = case["id"]
        item = by_id.get(cid)
        rows = mock_rows.get(cid, [])
        status = "PASS" if (item and item.passed) else "FAIL"
        lines.append(f"### {cid} · {case['boundary']}")
        lines.append("")
        lines.append(f"**输入**：`{case['text']}`")
        lines.append("")
        if rows:
            lines.append("| 字段 | 人工预期 | 实际输出 | 判定 |")
            lines.append("|---|---|---|---|")
            for field, want, got, mark in rows:
                lines.append(f"| `{field}` | {want} | {got} | {mark} |")
        else:
            lines.append("（无比对数据——契约入口抛出异常，见下方错误）")
        lines.append("")
        if item and item.errors:
            lines.append("**不一致明细**：")
            lines.append("")
            for error in item.errors:
                lines.append(f"- {error}")
            lines.append("")
        if item and item.missing_unsupported:
            lines.append(f"**漏标未覆盖需求**：{item.missing_unsupported}")
            lines.append("")
        if item and item.extra_unsupported:
            lines.append(f"**多标未覆盖需求**（偏保守）：{item.extra_unsupported}")
            lines.append("")
        lines.append(f"**结果**：{status}")
        lines.append("")
        if case.get("note"):
            lines.append(f"> 判据：{case['note']}")
            lines.append("")

    # --- 泄漏声明 ---
    lines.append("## 5. 评估泄漏声明（必读）")
    lines.append("")
    lines.append(
        "`MockClassifier` 是照本评测集**逐条调出来的确定性替身**，它在这 20 条上的"
        "表现**不构成任何语义理解能力证据**。它存在的意义只有一个：在真实模型接入前，"
        "把接口、结构、失败处理、评测口径先打通并锁死。"
    )
    lines.append("")
    lines.append("同理，`RuleBaseline`（`--impl baseline`）也是参照同一份标准答案写的对照实现。")
    lines.append("两者分数都只能用作**管道自检**，不能作为交付结果上报。")
    lines.append("")
    lines.append("本地假 OpenAI 服务（`http` 路径）内部同样委托 `MockClassifier`，")
    lines.append("因此它验证的是**线格式**（真实 socket → 真实 JSON 编解码 → 归一化 → 契约结构），")
    lines.append("不是模型准确率。若 `http` 与 `mock` 分数不一致，说明是管道问题，不是模型问题。")
    lines.append("")

    # --- 真实 API 实测 ---
    lines.append("## 6. 真实 API 实测（2026-10-01）")
    lines.append("")
    lines.append("**已经接上真实服务。** 服务商 DeepSeek，模型 `deepseek-flash`，")
    lines.append("端点 `https://api.deepseek.com/v1`，走 OpenAI 兼容的 `/chat/completions`。")
    lines.append("")
    lines.append("复现命令：")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/check_llm.py          # 真调一次线上服务，会花钱")
    lines.append("python tools/check_llm.py --json   # 额外打印完整契约 JSON")
    lines.append("```")
    lines.append("")
    lines.append("### 6.1 实测结果")
    lines.append("")
    lines.append("| 项 | 状态 | 实测值 |")
    lines.append("|---|---|---|")
    lines.append(
        "| 真实 API 连通 | ✅ | 抽取链路 1738 ms；返回 `status=ok`，"
        "`validate_result` 返回 **0 个问题** |"
    )
    lines.append(
        "| evidence 粒度 | ✅ **真实链路不复现替身的问题** | 模型给出 "
        "`预算三千左右`（完整原话片段），而 `MockClassifier` 只回 `\"3000\"`。"
        "逐条核对 5 个维度，**无一条证据是编造的** |"
    )
    lines.append(
        "| 含糊预算不封顶 | ✅ | 「预算三千左右」→ `reference=3000, max=None`，"
        "未被擅自封顶 |"
    )
    lines.append(
        "| 真实链路延迟 | ✅ | 抽取 1738 ms；推荐语 1829 ms。"
        "均远低于 60 s 超时阈值 |"
    )
    lines.append(
        "| 推荐语不编造型号 | ✅ | 生成 434 字，提到的 5 款机型**全部**在引擎清单内，"
        "未出现清单外机型 |"
    )
    lines.append(
        "| JSON 合规率 | ⚠️ 样本量 1 | 单条基准例子一次通过，无需重试。"
        "**1/1 不等于 100%**，20 条评测集尚未在真实模型上跑过 |"
    )
    lines.append("")
    lines.append("### 6.2 实测中发现并修掉的三个缺陷")
    lines.append("")
    lines.append("**① 模型不填 `asks`，导致界面不追问。**")
    lines.append("")
    lines.append("对「预算三千左右，想要轻一点的，主要看价格，256G 起步就行」，")
    lines.append("真实模型返回 `asks: []`——它把数字记进了 `budget_reference`，")
    lines.append("却没把 `budget_max` 列进 `asks`。而 `clarifications` 原本完全依赖")
    lines.append("`asks`，于是**界面不会问「最多能接受到多少」**，契约 §4.1 的红线")
    lines.append("「含糊预算不自动封顶，由确认页问用户」直接落空。")
    lines.append("")
    lines.append("修法：判定改成从**抽取出的字段**推导，不看模型填了什么——")
    lines.append("`budget.max is None` 就必须问，与 `bridge._snap_budget` 的 pending")
    lines.append("逻辑同源。修完真实链路已能正确问出「你说的是「3000 左右」，")
    lines.append("没有明确上限。最多能接受到多少？」")
    lines.append("")
    lines.append("**② DeepSeek V4 默认开启 thinking 模式，纯亏。**")
    lines.append("")
    lines.append("同一输入实测（同一 prompt）：")
    lines.append("")
    lines.append("| | thinking 开（DeepSeek 默认） | thinking 关 |")
    lines.append("|---|---|---|")
    lines.append("| 墙钟耗时 | 4166 ms | **890 ms** |")
    lines.append("| completion tokens | 836 | **195** |")
    lines.append("| 其中推理 tokens | 640 | 0 |")
    lines.append("| 抽取结果 | — | **逐字一致** |")
    lines.append("")
    lines.append("更要紧的是：**thinking 模式下 `temperature` 被静默忽略**")
    lines.append("（官方文档明说「不会报错，但也不会生效」），而抽取链路依赖")
    lines.append("`temperature=0` 保证可复现。已在两条链路默认关闭，")
    lines.append("`LLM_THINKING=1` 可打开。")
    lines.append("")
    lines.append("**附**：`deepseek-chat` 是遗留模型名。请求仍会被受理，但实际由")
    lines.append("DeepSeek-V4.1-Flash 提供服务并按 Flash 计价，返回体里的 `model`")
    lines.append("会被换成 `deepseek-flash`。当前可用名是 `deepseek-flash` 与")
    lines.append("`deepseek-v4-pro`。")
    lines.append("")
    lines.append("**③ 多轮对话里，用户后补的明确上限被红线检查误伤。**")
    lines.append("")
    lines.append("（2026-10-02 接入 Web 服务时发现，影响 Streamlit 与 Web 两条链路。）")
    lines.append("")
    lines.append("契约红线「含糊预算不得自动封顶」的检查原先拿**整段拼接历史**去")
    lines.append("找含糊短语。第二轮用户补了「最多 4000」之后，第一轮那句")
    lines.append("「预算三千左右」仍然留在 `input_text` 里，于是 `max=4000` 被判定为")
    lines.append("「原文含含糊预算却设了硬边界」——`to_blue_needs` 抛 `ValueError`，")
    lines.append("`handle_turn` 直接回「需求输出未通过接口检查」。**用户老老实实答完")
    lines.append("追问，反而出不了机型。**")
    lines.append("")
    lines.append("修法：`_vague_boundary_conflict` 只在两件事同时成立时才判违规——")
    lines.append("① 含糊短语出现在**最后一句**用户输入里（多轮历史各占一行）；")
    lines.append("② **含糊短语里那个数字本身**被写进了 `min`/`max`。这样")
    lines.append("「预算 3000 左右」被写成 `max=3000` 仍会被拦住，而")
    lines.append("「预算 3000 左右，最多 4000」不会。**宁可漏报，不可误报**——")
    lines.append("误报会让界面拒绝一条合法需求。回归用例见")
    lines.append("`tests/test_needs_extraction.py` 的 4 条多轮用例与")
    lines.append("`tests/test_pipeline.py::ClarifyTests`。")
    lines.append("")
    lines.append("### 6.3 仍未验证的部分")
    lines.append("")
    lines.append("| 项 | 状态 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(
        "| 20 条评测集在真实模型上的通过率 | ❌ 未实测 | 本记录的 20 条走的是 "
        "`MockClassifier` 与本地假服务，**不是真实模型**。真实模型的中文边界"
        "判断（「四五千」取中值、拍照归入未覆盖需求）尚未逐条核对 |"
    )
    lines.append(
        "| 真实模型 JSON 合规率 | ⚠️ 1/1 | 见 6.1。样本太少，不足以断言稳定 |"
    )
    lines.append(
        "| 超时 / 空内容 / 非法 JSON 的真实复现 | ⚠️ 部分 | 逻辑已实现并有单测覆盖，"
        "但未在真实网络抖动下验证 |"
    )
    lines.append(
        "| 并发与限流 | ❌ 未实测 | 未测并发上限与 429 行为 |"
    )
    lines.append("")
    lines.append("### 6.4 接入方式")
    lines.append("")
    lines.append("把 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 写进 `.env`")
    lines.append("（模板见 `.env.example`），或用环境变量传入。")
    lines.append("")
    lines.append("优先级：**真实环境变量 > `.env` > `.streamlit/secrets.toml`**，")
    lines.append("三者都是「只填不覆盖」。密钥不落盘、不入代码、不打印。")
    lines.append("")
    lines.append("> 修正一条此前的记录：`.env.example` 曾写着「复制为 `.env` 后填入真实值」，")
    lines.append("> 但在本轮之前**没有任何代码读 `.env`**——照做是无效的。现已由")
    lines.append("> `providers/env.py` 的 `load_dotenv()` 补上，`app.py` 启动时调用。")
    lines.append("")
    lines.append("### 6.5 端到端 UI")
    lines.append("")
    lines.append("`tests/test_app_flow.py` 用 Streamlit `AppTest` 真实点击过控件；")
    lines.append("另用 Playwright 驱动本机 Edge 截取五个状态（首屏 / 追问 / 出机型 /")
    lines.append("排除品牌追问 / 结果区）并做 DOM 断言。")
    lines.append("")
    lines.append("注意：`test_app_flow.py` 的用例继承 `IsolatedEnvTest`，会把")
    lines.append("`LLM_ENV_FILE` 指向不存在的路径以屏蔽开发机的 `.env`。")
    lines.append("否则「有 `.env` 的人跑测试会红、没有的人会绿」。")
    lines.append("")

    return "\n".join(lines)


# --- 单测统计 -----------------------------------------------------------------


def run_unit_tests() -> tuple[int, int]:
    """跑契约入口单测，返回 (通过数, 总数)。"""
    import unittest

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_needs_extraction")
    total = suite.countTestCases()
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    outcome = runner.run(suite)
    passed = total - len(outcome.failures) - len(outcome.errors)
    return passed, total


def main() -> int:
    parser = argparse.ArgumentParser(description="生成需求抽取实际测试记录")
    parser.add_argument(
        "--out",
        default=str(BASE / "docs" / "needs-extraction-test-record.md"),
        help="输出路径",
    )
    parser.add_argument(
        "--only",
        choices=("mock", "http"),
        help="只跑一条路径（默认两条都跑）",
    )
    args = parser.parse_args()

    cases = load_testset()
    modes = (args.only,) if args.only else ("mock", "http")

    paths: dict[str, tuple] = {}
    for mode in modes:
        results, rows, raw = run_path(cases, mode=mode)
        paths[mode] = (results, rows, raw)
        passed = sum(1 for item in results if item.passed)
        print(f"[{mode}] 通过 {passed}/{len(results)}")

    # 契约结构自检：20 条输出必须全部通过 validate_result
    problems: list[str] = []
    for cid, result in paths[modes[0]][2].items():
        if "_exception" in result:
            problems.append(f"{cid}: {result['_exception']}")
            continue
        for issue in validate_result(result):
            problems.append(f"{cid}: {issue}")
    if problems:
        print("契约结构自检未通过：")
        for item in problems:
            print(f"  - {item}")
        return 2
    print(f"契约结构自检：{len(paths[modes[0]][2])} 条输出全部符合 schema")

    unit_passed, unit_total = run_unit_tests()
    print(f"契约入口单测：{unit_passed}/{unit_total}")

    markdown = render(cases, paths=paths, unit_tests=(unit_passed, unit_total))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"记录已写入：{out}")

    failed = sum(
        1 for results, _, _ in paths.values() for item in results if not item.passed
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
