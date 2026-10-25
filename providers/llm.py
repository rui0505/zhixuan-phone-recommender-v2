"""外部大模型 API 适配层（OpenAI 兼容的 chat/completions）。

与 `providers/jev.py` 的 `HttpClassifier` 的分工：

- `HttpClassifier` 面向**已经做好需求抽取**的专用服务：请求体是
  `{text, criteria, choices}`，服务端直接返回结构化结果。
- 本模块面向**通用大模型 API**（DeepSeek / 通义 / 智谱 / 硅基流动 等
  OpenAI 兼容端点）：自己组装 prompt 让模型产出 JSON，再解析。

两者共用 `providers.jev.normalise_response` 做归一化，保证
「非法 choice 一律退回未提及」「evidence 对不上原文就降级为 inferred」
这两条安全规则只有一份实现，不会在两个 provider 之间分叉。

配置全部走环境变量，密钥不落盘、不入代码、不打印：

    LLM_BASE_URL   例如 https://api.deepseek.com/v1
    LLM_MODEL      例如 deepseek-flash
    LLM_API_KEY    密钥
    LLM_THINKING   可选，`1` 打开 thinking 模式（默认关闭，见 `thinking_payload`）

注意模型名：DeepSeek 的 `deepseek-chat` 是**遗留名**，请求仍会被受理，但实际
由 DeepSeek-V4.1-Flash 提供服务并按 Flash 计价。当前可用名是 `deepseek-flash`
与 `deepseek-v4-pro`。

本模块**只读环境变量，不自己加载 `.env`**。加载是入口处的显式动作
（`app.py` 的 `_prepare_llm_env()`、`tools/check_llm.py`），走
`providers/env.py` 的 `load_dotenv()`。刻意不在这里隐式加载：单测用
`mock.patch.dict(clear=True)` 模拟「未配置」，隐式加载会让本机真实的
`.env` 污染这些用例。

用法：

    from providers.jev import parse_needs
    from providers.llm import LLMClassifier

    needs = parse_needs('预算三千左右，想要轻一点的', classifier=LLMClassifier())
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterable, Mapping, MutableMapping

from providers.jev import JevUnavailable, JevResult, normalise_response

# --- Prompt -----------------------------------------------------------------
#
# 约束按「模型最容易犯的错」排序：含糊预算擅自设上限、把未提及当不在意、
# 漏标未覆盖需求。这三类错误会直接生成虚假结论，因此写得最重。

# Prompt 版本号。**改动 SYSTEM_PROMPT 必须同步升版本**，并写进返回结构，
# 否则无法追溯某次结果是哪个 prompt 产出的。命名规则：extract-needs-v<主>.<次>。
PROMPT_VERSION = "extract-needs-v1.2"

SYSTEM_PROMPT = """\
你是手机选购需求的结构化抽取器。只做抽取，不做推荐，不替用户补充他没说出口的偏好。
prompt_version: extract-needs-v1.2

## 多轮补充
输入可能是用户**先后说的几句话**，用「1. 2. 3.」编号。此时：
- **后面的更准**，前后冲突时以后者为准（先说「2000 左右」后说「最多 3000」→ 上限 3000）。
- 每一句都是**用户本人**说的，不要当成问答记录。
- 早期提到的、后面没有推翻的条件仍然有效。

## 偏好维度（8 个，每个都必须给判断；不表示数据库已支持排序）
price=价格更低　weight=机身更轻　storage=存储更大　refresh=刷新率更高　screen=屏幕接近偏好
game=游戏表现　photo=拍照表现　battery=续航表现
游戏、拍照、续航没提到必须填未提及；基础偏好默认1、新性能偏好默认0，由代码映射。
你只抽取用户愿望，不能判断数据库覆盖、商品表现、排名或生成数值权重。
显式「拍照最重要」=非常重要/extracted；「续航重要」=重要/extracted。
「爱打和平精英」支持游戏用途：game=重要/inferred，证据保留「爱打和平精英」，需用户确认强度与范围。
所有推断必须 verdict=inferred，不得伪装显式最高等级；未提及用unknown，明确表达用extracted。
当前游戏指标只指同条件王者荣耀平均帧率；不得把和平精英、其他游戏或泛游戏用途改写为王者荣耀。
照片仅指同版DXOMARK照片子分，不代表视频/自拍；电池容量和充电功率不得代替实测续航。

## 取值（只能从这五个里选，一个都不能少）
未提及 / 不在意 / 一般 / 重要 / 非常重要

强度口径（同句式必须给同一强度，不得因措辞微调而升降级）：
- 非常重要：含最高级或强制表达。「最轻」「必须轻」「价格最重要」
- 重要：明确方向性偏好。「越轻越好」「重量要轻」「越便宜越好」
- 一般：弱方向提示或显式低强度。「别太重」「轻一点」「一般就行」
- 不在意：用户**主动否定**该维度。「无所谓」「不重要」「不太在意」
- 未提及：用户**根本没提到**该维度。

⚠️「未提及」与「不在意」是两件完全不同的事。没提到就是未提及，
不要因为用户夸了别的维度就推断这一维，也不要因为觉得「他应该不在乎」就填不在意。

## 预算（三个字段，最容易出错，逐字读）
- budget_reference：用户提到的**单一锚点数字**。句子里只要出现预算数字就填。
  「2000左右」→2000　「三千多」→3000　「四五千」→4500（取中值）　「预算5000」→5000
  有明确区间时留 null —— 区间已由 min/max 表达，不要自己编中值。
- budget_min / budget_max：**只有三种表达算明确硬约束**：
  · 「N 到 M」区间　　　　　　→ min=N, max=M
  · 「N以内 / 以下 / 不超过 N」→ max=N
  · 「N以上 / 不低于 N」　　　 → min=N
  ⚠️「2000左右」「三千多」「四五千」「预算5000」**一律不写 min/max**，
  只填 budget_reference。含糊预算不自动封顶，由确认页问用户。
  ⚠️ 不要把存储门槛（「256起步」）或屏幕尺寸（「6.2英寸」）当成预算。

## 其他硬约束（只抽明确表达的，没提到就给 null 或空数组）
- min_storage：存储门槛。「256G起步」「至少256G」→ 256
- brands：品牌白名单。「只看小米」→ ["小米"]
- target_screen：明确说出的屏幕尺寸（英寸）。「6.5英寸」→ 6.5

## 其他字段
- excluded_brands：被明确排除的品牌。「不要华为」→ ["华为"]。
  ⚠️ 排除项**不要**放进 brands。
- unsupported_needs：数据覆盖不到的需求。只能取这几个值：
  影像 / 续航 / 真实游戏性能 / 分辨率 / 机身尺寸手感 / 信号质量 / 处理器性能。
  出现相关词（拍照、续航、打游戏、性价比…）就必须标。**漏标比多标严重得多**，
  漏标会生成虚假结论。拿不准就标上。
- asks：无法可靠提取、但会显著改变候选集的字段名。只能取：
  budget_max / min_storage / target_screen / excluded_brands

## 门槛与偏好要分开
「256G起步」是门槛（进 min_storage），不是偏好。「存储越大越好」才是偏好（storage=重要）。

## evidence
必须是**用户原话里的连续片段**，逐字复制，不要改写、不要概括、不要自己造。
原文里没有对应片段就留空字符串。
judgements 里每项的 evidence 对应各自的偏好维度；顶层 evidence 对象的四项
分别对应 budget / min_storage / target_screen / brands 这些硬约束。

## 输出
只输出一个 JSON 对象。不要解释、不要 markdown 代码块、不要前后缀文字。
judgements 里 8 个维度必须齐全，顺序不限。每项只含criterion、choice、evidence、verdict。

{"judgements":[{"criterion":"price","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"weight","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"storage","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"refresh","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"screen","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"game","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"photo","choice":"未提及","evidence":"","verdict":"unknown"},{"criterion":"battery","choice":"未提及","evidence":"","verdict":"unknown"}],"hard_constraints":{"budget_min":null,"budget_max":null,"budget_reference":null,"min_storage":null,"brands":[],"target_screen":null},"evidence":{"budget":"","min_storage":"","target_screen":"","brands":""},"excluded_brands":[],"asks":[],"unsupported_needs":[]}\
"""

USER_TEMPLATE = "用户需求原文：\n{text}\n\n按上述 JSON 结构输出。"


def extract_json_object(content: str) -> Mapping[str, Any]:
    """从模型回复里抠出 JSON 对象。

    模型常把 JSON 包在 ```json 围栏里，或在前后加解释文字，所以不能直接
    json.loads。这里先剥围栏，失败再退化为「取第一个括号平衡的 {...}」。
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("模型回复中没有 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(text[start : index + 1])
                if not isinstance(parsed, Mapping):
                    raise ValueError("JSON 顶层不是对象")
                return parsed
    raise ValueError("JSON 对象不完整（括号未闭合）")


def post_chat(
    endpoint: str,
    payload: Mapping[str, Any],
    key: str,
    *,
    timeout: float = 60.0,
    retries: int = 1,
) -> tuple[Mapping[str, Any] | None, Exception | None]:
    """发一次 chat/completions 请求，失败重试。返回 (响应体, 最后一次异常)。

    抽成模块级是为了让「需求抽取」与「推荐语生成」两条链路共用同一份
    HTTP 逻辑：重试策略、400 降级、密钥只进请求头，都只有一处实现。

    全部失败时返回 `(None, 异常)`；`400` 视为「参数不被支持」，
    立即返回 `(None, None)` 交给调用方去掉 `response_format` 重试。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last: Exception | None = None
    for _ in range(max(0, retries) + 1):
        request = urllib.request.Request(endpoint, data=data, method="POST")
        # 令牌只存在于请求头，不打印、不落盘
        request.add_header("Authorization", f"Bearer {key}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")), None
        except urllib.error.HTTPError as error:
            error.close()  # HTTPError owns an open response even when urlopen raises.
            # 400 通常是参数不被支持，交给调用方降级重试
            if error.code == 400:
                return None, None
            last = error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last = error
    return None, last


# 可选的请求字段，按「先丢哪个」的顺序排列。
#
# 这些字段不是所有 OpenAI 兼容端点都认。不认的端点会回 400，此时逐项剥掉
# 重试，而不是直接判定服务不可用——否则换个供应商就整条链路挂掉。
#
# 顺序有讲究：`response_format` 影响输出形态，丢了仍能靠 `extract_json_object`
# 从散文里抠 JSON；`thinking` 只影响速度与成本，丢了没有任何功能损失。
# 所以先丢 `response_format`，实在不行再丢 `thinking`。
OPTIONAL_PAYLOAD_FIELDS = ("response_format", "thinking")


def thinking_from_env() -> bool:
    """是否开启 thinking 模式。默认关闭，`LLM_THINKING=1` 打开。"""
    return os.environ.get("LLM_THINKING", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def thinking_payload(enabled: bool) -> dict[str, Any]:
    """构造 DeepSeek 的 thinking 开关字段。

    ## 为什么默认关闭

    DeepSeek V4 默认开启 thinking 模式，effort 默认 `high`。对本项目是纯亏。
    同一输入实测（deepseek-flash，同一 prompt）：

    | | thinking 开（默认） | thinking 关 |
    |---|---|---|
    | 墙钟耗时 | 4166 ms | **890 ms** |
    | completion tokens | 836 | **195** |
    | 其中推理 tokens | 640 | 0 |
    | 抽取结果 | — | **逐字一致** |

    更要紧的一条：**thinking 模式下 `temperature` 被静默忽略**（官方文档明说
    「不会报错，但也不会生效」）。而抽取链路依赖 `temperature=0` 保证可复现——
    开着 thinking 会让这个设置形同虚设，且没有任何提示。

    需要时用 `LLM_THINKING=1` 打开。
    """
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


def post_chat_resilient(
    endpoint: str,
    payload: MutableMapping[str, Any],
    key: str,
    *,
    optional: Iterable[str] = OPTIONAL_PAYLOAD_FIELDS,
    timeout: float = 60.0,
    retries: int = 1,
) -> tuple[Mapping[str, Any] | None, Exception | None, list[str]]:
    """发请求；遇到 400（参数不被支持）就逐项剥掉可选字段再试。

    返回 `(响应体, 最后一次异常, 被剥掉的字段名)`。

    注意 `post_chat` 用 `(None, None)` 唯一地表示 400，所以循环条件
    `body is None and error is None` 就等价于「又吃了一个 400」。
    """
    dropped: list[str] = []
    body, error = post_chat(endpoint, payload, key, timeout=timeout, retries=retries)
    for field in optional:
        if body is not None or error is not None:
            break
        if field not in payload:
            continue
        payload.pop(field, None)
        dropped.append(field)
        body, error = post_chat(endpoint, payload, key, timeout=timeout, retries=retries)
    return body, error, dropped


def content_of(body: Mapping[str, Any]) -> str:
    """取出回复正文。兼容 content 是字符串或分段数组两种形态。"""
    choices = body.get("choices") or []
    if not choices:
        raise JevUnavailable("LLM 回复缺少 choices。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, Mapping)
        )
    return ""


class LLMClassifier:
    """OpenAI 兼容大模型 API 的需求抽取实现。

    只负责「取回并解析」，业务判断交给 `normalise_response`。
    任何失败都抛 `JevUnavailable`，由调用方回退到表单/固定问卷路线——
    与 `HttpClassifier` 的契约保持一致。
    """

    name = "llm-api"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "LLM_API_KEY",
        timeout: float = 60.0,
        retries: int = 1,
        temperature: float = 0.0,
        max_tokens: int = 900,
        json_mode: bool = True,
        thinking: bool | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.retries = max(0, retries)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        # None 表示「看环境变量」，显式 True/False 则以此为准
        self.thinking = thinking_from_env() if thinking is None else thinking
        if not self.base_url:
            raise JevUnavailable(
                "未配置 LLM_BASE_URL。请设置环境变量后重试；"
                "未接入真实服务时应使用 MockClassifier 或表单路线。"
            )
        if not self.model:
            raise JevUnavailable("未配置 LLM_MODEL。")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def classify(self, text: str) -> JevResult:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise JevUnavailable(
                f"环境变量 {self.api_key_env} 未设置。密钥不写入代码或日志。"
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(text=text)},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **thinking_payload(self.thinking),
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        body, last_error, _dropped = post_chat_resilient(
            self.endpoint, payload, key, timeout=self.timeout, retries=self.retries
        )
        if body is None:
            raise JevUnavailable(_failure_message(last_error))

        content = content_of(body)
        try:
            parsed = extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as error:
            raise JevUnavailable(f"模型回复不是合法 JSON：{error}") from error

        return normalise_response(
            parsed,
            provider=self.name,
            model_version=str(body.get("model", self.model)),
            source_text=text,
        )

    @staticmethod
    def _content_of(body: Mapping[str, Any]) -> str:
        """兼容旧调用点，实际实现在模块级 `content_of`。"""
        return content_of(body)


def _failure_message(error: Exception | None) -> str:
    """把最后一次异常转成可读信息。**不得包含密钥**——密钥只在请求头里。"""
    if error is None:
        return "LLM 服务调用失败（已重试）。"
    return f"LLM 服务调用失败（已重试）：{type(error).__name__}: {error}"


def llm_status(environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """检查大模型配置是否齐全，返回 `(是否可用, 说明)`。

    **只检查配置，不发真实请求**——界面加载不该产生调用费用。
    真调一次请用 `python tools/check_llm.py`。

    Streamlit 界面与 Web 服务共用这一份判定，避免两边对「能不能用」给出
    不同答案。返回的说明**不含密钥**，可以直接渲染到页面上。
    """
    env = os.environ if environ is None else environ
    base_url = (env.get("LLM_BASE_URL") or "").strip()
    model = (env.get("LLM_MODEL") or "").strip()
    api_key = (env.get("LLM_API_KEY") or "").strip()

    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", base_url),
            ("LLM_MODEL", model),
            ("LLM_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        return False, (
            "未配置 " + "、".join(missing)
            + "。密钥只放环境变量或 .env，不写进代码。"
        )
    return True, f"已接入大模型：{model}　（{base_url.rstrip('/')}）"


__all__ = [
    "LLMClassifier",
    "OPTIONAL_PAYLOAD_FIELDS",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "content_of",
    "extract_json_object",
    "llm_status",
    "post_chat",
    "post_chat_resilient",
    "thinking_from_env",
    "thinking_payload",
]
