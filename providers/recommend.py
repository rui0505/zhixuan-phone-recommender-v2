"""推荐语生成：把**引擎算出的真实结果**交给大模型，让它写成一段人话。

## 为什么大模型不能自己报机型

本地数据表里有 46 款机型、156 条配置，大模型完全不知道。让它直接推荐，
它会编造型号、价格和参数——用户点进去对不上，是纯粹的幻觉。

所以分工是死的：

    大模型：理解需求（`needs_extraction`） + 把真实结果写成一段话（本模块）
    引擎：  筛选 + TOPSIS 排序 + 给出机型、价格、参数（`engine.rank`）

本模块的输入是**已经算好的事实清单**，输出只是一段措辞。模型没有任何
机会引入新事实——清单里没有的型号、价格、参数，它一个都不许提。

## 防幻觉的三道闸

1. **prompt 硬约束**：明确列出禁止事项（不得提清单外机型、不得编造参数、
   不得把相对匹配度说成质量评分）。
2. **事实清单结构化**：机型逐条编号给出，参数写死，模型只需挑选和解释。
3. **界面兜底**：本模块**永不抛异常**，失败返回空串；界面用模板文案顶上，
   并始终把引擎的机型卡片渲染在推荐语下方——**以卡片为准，不以推荐语为准**。

第 3 条是关键：推荐语只是「解说」，机型列表永远来自引擎。即使模型胡说，
用户看到的机型也不会错。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from providers.jev import JevUnavailable
from providers.llm import (
    content_of,
    post_chat_resilient,
    thinking_from_env,
    thinking_payload,
)

# Prompt 版本号。**改动 RECOMMEND_SYSTEM_PROMPT 必须同步升版本**。
RECOMMEND_PROMPT_VERSION = "recommend-v1.0"

RECOMMEND_SYSTEM_PROMPT = """\
你是手机推荐结果的解说员。你不是推荐算法，也不做推荐——机型已经由系统算好了。

prompt_version: recommend-v1.0

## 你的任务
把「本次推荐结果」写成一段给用户看的中文说明，3–5 句话。说清楚：
为什么是这几款、它们各自适合什么样的人、以及哪些需求这次帮不上忙。

## 绝对禁止（违反即视为失败）
1. **不得提到清单之外的任何机型**。一个都不许。想举例也不行。
2. **不得编造任何参数**。清单里没写的（拍照、续航、游戏性能、信号、
   散热、系统体验等）一律不许评价、不许推测。
3. **不得把「匹配度」说成质量分、评分或购买概率**。它是本次候选集内的
   相对得分，候选集一变就会变。
4. **不得替用户下结论**（如「这就是最适合你的」「闭眼买」）。
5. 不得使用「最佳」「最强」「第一」这类没有数据支撑的形容词。

## 语气
直接、克制、像懂行的朋友在解释，不像广告。不要用感叹号堆砌，
不要用「总之」「综上所述」这类套话。不要重复用户已经知道的信息。

## 覆盖不到的需求怎么处理
清单里如果列了「本次无法评价的需求」，必须明确告诉用户这些需求现有数据
帮不上忙，**并且不得用别的指标替代**（拍照好 ≠ 摄像头像素高，
续航久 ≠ 电池容量大，游戏流畅 ≠ 刷新率高）。

## 输出
只输出这段中文说明本身。不要 JSON、不要标题、不要 markdown 列表、
不要前后缀解释。
"""

RECOMMEND_USER_TEMPLATE = """\
以下是本次推荐结果。请据此写一段说明。

{facts}
"""

# 引擎里的匹配度字段名 → 给模型看的说法。避免模型把它当成质量分。
_SCORE_LABEL = "TOPSIS 相对匹配度（0–1，仅在本候选集内有意义）"


@dataclass
class Brief:
    """交给大模型的**事实清单**。所有字段都必须是引擎算出的真实数据。"""

    models: list[dict[str, Any]] = field(default_factory=list)
    needs_summary: str = ""
    pending: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    candidate_configs: int = 0
    total_configs: int = 0
    budget: tuple[float, float] | None = None

    def is_empty(self) -> bool:
        return not self.models


def render_facts(brief: Brief) -> str:
    """把事实清单渲染成模型易读的纯文本。

    刻意用「编号 + 字段名: 值」的平铺格式，而不是 JSON：模型对平铺文本
    的忠实度更高，也更不容易自己「补全」缺失字段。
    """
    lines: list[str] = []

    lines.append("【用户需求】")
    lines.append(brief.needs_summary or "（未提取到明确需求，按系统默认条件推荐）")
    if brief.budget is not None:
        low, high = brief.budget
        lines.append(f"价格区间: ¥{low:,.0f} – ¥{high:,.0f}")
    lines.append("")

    lines.append("【候选规模】")
    lines.append(
        f"全库 {brief.total_configs} 条配置，本次筛选后 {brief.candidate_configs} 条"
    )
    lines.append("")

    lines.append("【推荐机型清单】只能提下面这些，一个都不许多：")
    for index, model in enumerate(brief.models, 1):
        lines.append(f"{index}. 机型名: {model.get('name', '')}")
        lines.append(f"   配置: {model.get('config', '')}")
        lines.append(f"   表内价格: ¥{model.get('price', 0):,.0f}")
        lines.append(f"   处理器: {model.get('chip', '') or '数据缺失'}")
        lines.append(f"   重量: {model.get('weight', 0):g} 克")
        lines.append(f"   屏幕: {model.get('screen', 0):g} 英寸")
        lines.append(f"   刷新率: {model.get('refresh', 0):g} Hz")
        lines.append(f"   存储: {model.get('storage', 0):g} GB")
        lines.append(f"   {_SCORE_LABEL}: {model.get('score', 0):.3f}")
        if model.get("reason"):
            lines.append(f"   参数差异: {model['reason']}")
    lines.append("")

    if brief.pending:
        lines.append("【尚未确认的条件】")
        for item in brief.pending:
            lines.append(f"- {item}")
        lines.append("（这些是系统没有可靠依据、留给用户确认的，说明里可以提一句）")
        lines.append("")

    if brief.unsupported:
        lines.append("【本次无法评价的需求】必须明确告诉用户帮不上忙：")
        for item in brief.unsupported:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def _clean(text: str) -> str:
    """剥掉模型可能加上的 markdown 围栏与标题。"""
    out = (text or "").strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1] if "\n" in out else ""
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    out = re.sub(r"^\s*#{1,6}\s*", "", out, flags=re.MULTILINE)
    return out.strip()


class LLMWriter:
    """推荐语写手。配置全部走环境变量，与 `LLMClassifier` 同一套。"""

    name = "llm-writer"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "LLM_API_KEY",
        timeout: float = 60.0,
        retries: int = 1,
        temperature: float = 0.3,
        max_tokens: int = 600,
        thinking: bool | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 与 LLMClassifier 同一套开关：默认关闭 thinking。
        # 推荐语是短散文，更不需要多步推理；而 thinking 模式下 temperature
        # 会被静默忽略，这里设的 0.3 就白设了。
        self.thinking = thinking_from_env() if thinking is None else thinking
        if not self.base_url:
            raise JevUnavailable(
                "未配置 LLM_BASE_URL。请设置环境变量后重试；"
                "未接入真实服务时界面会自动改用模板文案。"
            )
        if not self.model:
            raise JevUnavailable("未配置 LLM_MODEL。")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def write(self, brief: Brief) -> str:
        """生成推荐语。失败抛 `JevUnavailable`，由 `write_recommendation` 兜住。"""
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise JevUnavailable(
                f"环境变量 {self.api_key_env} 未设置。密钥不写入代码或日志。"
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": RECOMMEND_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": RECOMMEND_USER_TEMPLATE.format(
                        facts=render_facts(brief)
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **thinking_payload(self.thinking),
        }

        body, last_error, _dropped = post_chat_resilient(
            self.endpoint, payload, key, timeout=self.timeout, retries=self.retries
        )
        if body is None:
            raise JevUnavailable(
                "推荐语生成失败（已重试）。"
                if last_error is None
                else f"推荐语生成失败：{type(last_error).__name__}: {last_error}"
            )

        return _clean(content_of(body))


def write_recommendation(
    brief: Brief,
    *,
    writer: Any = None,
    timeout: float | None = None,
) -> tuple[str, str]:
    """生成推荐语。**永不抛异常。**

    返回 `(文案, 错误信息)`：
    - 成功 → `(文案, "")`
    - 失败 → `("", 原因)`，界面据此改用模板文案

    清单为空、未配置大模型、上游异常，一律走失败分支——推荐语只是锦上添花，
    绝不能因为它挂掉就挡住机型列表。
    """
    if brief.is_empty():
        return "", "没有可推荐的机型，无需生成推荐语"

    try:
        if writer is None:
            writer = LLMWriter(**({"timeout": timeout} if timeout else {}))
        text = _clean(writer.write(brief))
    except JevUnavailable as error:
        return "", str(error)
    except Exception as error:  # noqa: BLE001 - 契约要求永不抛
        return "", f"{type(error).__name__}: {error}"

    if not text:
        return "", "模型返回了空内容"
    return text, ""


def fallback_text(brief: Brief) -> str:
    """大模型不可用时的模板文案。

    只用引擎给的真实数字，措辞刻意朴素——用户能一眼看出这是模板，
    不会误以为是大模型写的。
    """
    if brief.is_empty():
        return "当前条件下没有候选机型，请放宽预算、品牌或最低存储要求。"

    count = len(brief.models)
    lines = [f"按你的条件筛选后，从 {brief.candidate_configs} 条配置里挑出这 {count} 款："]
    for index, model in enumerate(brief.models, 1):
        lines.append(
            f"{index}. {model.get('name', '')}（{model.get('config', '')}）"
            f"　¥{model.get('price', 0):,.0f}"
        )
    if brief.pending:
        lines.append("还有这些条件我没法确定：" + "、".join(brief.pending) + "。")
    if brief.unsupported:
        lines.append(
            "另外，" + "、".join(brief.unsupported) + "这些需求现有数据覆盖不到，"
            "不会用别的指标替代。"
        )
    lines.append("（当前未接入大模型，以上为模板文案；机型与参数来自本地数据表。）")
    return "\n".join(lines)


__all__ = [
    "Brief",
    "LLMWriter",
    "RECOMMEND_PROMPT_VERSION",
    "RECOMMEND_SYSTEM_PROMPT",
    "RECOMMEND_USER_TEMPLATE",
    "fallback_text",
    "render_facts",
    "write_recommendation",
]
