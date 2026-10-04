"""命令行需求抽取示例：中文自由文本 → 结构化 Needs。

这是「外接 API + prompt」路线的最小可运行样例，也是排查问题的第一现场。

用法（在 phone-demo 目录）：

    # 1. 完全离线，看链路长什么样（无需密钥，用确定性替身）
    python tools/extract.py --offline "预算三千左右，主要看价格，越便宜越好"

    # 2. 真实调用外接大模型（需先设 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）
    python tools/extract.py "预算三千左右，主要看价格，越便宜越好"

    # 3. 看看到底发了什么请求、收到什么回复（密钥自动打码）
    python tools/extract.py --raw --offline "不要华为，存储 512G 以上"

    # 4. 只看 prompt
    python tools/extract.py --show-prompt

    # 5. 批量跑一组内置示例
    python tools/extract.py --offline --examples
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# Windows 控制台默认 GBK，中文会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from providers.jev import CRITERIA, JevUnavailable, MockClassifier, parse_needs  # noqa: E402
from schemas import LEVEL_MAP, UNMENTIONED_LEVEL  # noqa: E402

CRITERION_LABEL = {
    "price": "价格更低",
    "weight": "机身更轻",
    "storage": "存储更大",
    "refresh": "刷新率更高",
    "screen": "屏幕接近偏好",
}

VERDICT_LABEL = {
    "extracted": "原文直取",
    "inferred": "推断",
    "unknown": "未知",
}

EXAMPLES = [
    "预算三千左右，主要看价格，越便宜越好",
    "想买个 iPhone，256GB 起步，预算 8000 以内",
    "不要华为，其他牌子都行，存储 512G 以上，价格四五千",
    "打游戏不卡就行，刷新率要高，一加或者红米",
    "什么都行，你帮我决定吧",
    "要 iPhone，存储越大越好，最少 256G，预算 6000 到 9000",
]


# --- 本地假服务 -------------------------------------------------------------
#
# 实现放在 tests/fake_openai.py，与离线集成测试共用一份，避免两处逻辑分叉。

sys.path.insert(0, str(BASE / "tests"))

from fake_openai import FakeOpenAIServer  # noqa: E402


# --- 输出 -------------------------------------------------------------------


def show_prompt() -> None:
    from providers.llm import SYSTEM_PROMPT, USER_TEMPLATE

    print("=" * 72)
    print("SYSTEM PROMPT")
    print("=" * 72)
    print(SYSTEM_PROMPT)
    print("=" * 72)
    print("USER TEMPLATE")
    print("=" * 72)
    print(USER_TEMPLATE)
    print()
    print(f"system prompt 长度：{len(SYSTEM_PROMPT)} 字符")
    print(f"temperature=0.0，max_tokens=900，response_format={{'type': 'json_object'}}")


def show_result(text: str, needs, classifier_name: str) -> None:
    print()
    print("输入　" + text)
    print("实现　" + classifier_name)
    print()
    print("── 偏好（5 维，权重来源）" + "─" * 40)
    for key in CRITERIA:
        item = needs.preference_for(key)
        if item is None:
            continue
        level = LEVEL_MAP.get(item.choice, UNMENTIONED_LEVEL)
        evidence = f'　← "{item.raw_text}"' if item.raw_text else ""
        mark = "·" if item.choice == "未提及" else "●"
        print(
            f"  {mark} {key:<8} {item.choice:<5} 强度{level}"
            f"  [{VERDICT_LABEL.get(item.verdict, item.verdict)}]{evidence}"
        )

    print()
    print("── 硬约束（只记明确表达，不猜）" + "─" * 36)
    hard = needs.hard
    rows = [
        ("预算下界", hard.budget_min, "元"),
        ("预算上界", hard.budget_max, "元"),
        ("存储门槛", hard.min_storage, "GB"),
        ("屏幕尺寸", hard.target_screen, "英寸"),
        ("品牌白名单", list(hard.brands) or None, ""),
    ]
    for label, value, unit in rows:
        shown = "null" if value is None else f"{value}{unit}"
        print(f"  {label:<10} {shown}")

    print()
    if needs.needs_confirmation:
        print(f"── 必须回问用户：{', '.join(needs.needs_confirmation)}")
    else:
        print("── 必须回问用户：无")

    if needs.unsupported_needs:
        print(f"── 数据覆盖不到：{', '.join(needs.unsupported_needs)}")
    else:
        print("── 数据覆盖不到：无")
    print()


# --- 原始请求/回复抓取 -------------------------------------------------------


class WireRecorder:
    """包住 urlopen，记录请求体与响应体，密钥打码。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self):
        import providers.llm as module

        self._module = module
        self._original = module.urllib.request.urlopen

        def wrapper(request, *args, **kwargs):
            body = json.loads(request.data.decode("utf-8")) if request.data else {}
            entry = {
                "url": request.full_url,
                "headers": {
                    k: ("Bearer sk-***打码***" if k.lower() == "authorization" else v)
                    for k, v in request.header_items()
                },
                "payload": body,
            }
            response = self._original(request, *args, **kwargs)
            entry["response"] = json.loads(response.read().decode("utf-8"))
            self.calls.append(entry)
            return _Replay(response, entry["response"])

        module.urllib.request.urlopen = wrapper
        return self

    def __exit__(self, *exc):
        self._module.urllib.request.urlopen = self._original
        return False


class _Replay:
    """把已经读过的响应重新包一层，供上层再读一次。"""

    def __init__(self, original, payload: Mapping[str, Any]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._original = original

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def dump_wire(calls: list[dict[str, Any]]) -> None:
    print("=" * 72)
    print(f"实际发出的 HTTP 请求（共 {len(calls)} 次）")
    print("=" * 72)
    for index, call in enumerate(calls, 1):
        print(f"\n【第 {index} 次】POST {call['url']}")
        print("请求头：")
        for key, value in call["headers"].items():
            print(f"  {key}: {value}")
        print("请求体：")
        print("  " + json.dumps(call["payload"], ensure_ascii=False, indent=2)[:900])
        print("回复：")
        print("  " + json.dumps(call["response"], ensure_ascii=False, indent=2)[:900])


# --- 主流程 -----------------------------------------------------------------


def build_classifier(offline: bool):
    if offline:
        return MockClassifier(), "MockClassifier（离线替身，不联网）"
    from providers.llm import LLMClassifier

    classifier = LLMClassifier()
    return classifier, f"LLMClassifier（{classifier.model} @ {classifier.base_url}）"


def run_one(text: str, classifier, label: str, raw: bool) -> int:
    if raw and not isinstance(classifier, MockClassifier):
        with WireRecorder() as recorder:
            needs = parse_needs(text, classifier=classifier)
        show_result(text, needs, label)
        dump_wire(recorder.calls)
    else:
        needs = parse_needs(text, classifier=classifier)
        show_result(text, needs, label)
        if raw:
            print("（--offline 模式没有真实 HTTP 往返，故不打印请求）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="中文需求抽取示例",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("text", nargs="?", help="中文需求原文")
    parser.add_argument("--offline", action="store_true", help="用离线替身，无需密钥")
    parser.add_argument(
        "--fake-server",
        action="store_true",
        help="本地假服务：走真实 HTTP 往返，但不需要密钥（配合 --raw 看报文）",
    )
    parser.add_argument("--raw", action="store_true", help="打印真实 HTTP 请求与回复")
    parser.add_argument("--show-prompt", action="store_true", help="只打印 prompt")
    parser.add_argument("--examples", action="store_true", help="跑一组内置示例")
    args = parser.parse_args()

    if args.show_prompt:
        show_prompt()
        return 0

    if not args.text and not args.examples:
        parser.print_help()
        return 1

    import contextlib

    with contextlib.ExitStack() as stack:
        if args.fake_server:
            server = stack.enter_context(FakeOpenAIServer())
            print(f"本地假服务已启动：http://127.0.0.1:{server.port}/v1")
            print("（走真实 HTTP 往返，但不需要密钥）")
            args.offline = False

        try:
            classifier, label = build_classifier(args.offline)
        except JevUnavailable as error:
            print(f"无法初始化：{error}")
            print()
            print("先设置环境变量：")
            print("  LLM_BASE_URL=https://api.deepseek.com/v1")
            print("  LLM_MODEL=deepseek-chat")
            print("  LLM_API_KEY=<你的密钥>")
            print()
            print("或者加 --offline 用离线替身先看链路；")
            print("或者加 --fake-server 走真实 HTTP 但不需要密钥。")
            return 2

        texts = EXAMPLES if args.examples else [args.text]
        for index, text in enumerate(texts, 1):
            if len(texts) > 1:
                print()
                print(f"########## 示例 {index}/{len(texts)} ##########")
            try:
                run_one(text, classifier, label, args.raw)
            except JevUnavailable as error:
                print(f"  调用失败：{error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
