"""可复用的推荐管线：中文需求 → 真实机型。

Streamlit 界面（`app.py`）与 Web 服务（`web/server.py`）共用本模块。
这里只做「数据进、结构化结果出」，**不 import 任何界面框架**，也不碰
`st.session_state` —— 界面自己的状态由界面自己管。

## 职责三分（与 `providers/recommend.py` 同一套约定）

    大模型：理解中文需求（`needs_extraction`）+ 把结果写成一段话（`providers.recommend`）
    本模块：串起 抽取 → 契约适配 → 筛选 → TOPSIS 排序 → 事实清单
    引擎：  真正算分与出机型（`engine.rank`）

**机型永远由 `engine.rank` 产出，不由大模型报。** 本模块不生成任何机型名，
只搬运引擎结果。

## 为什么要有这一层

`app.py` 原先把这套流程内联在 `handle_turn` 里。Web 服务要复用就得复制一份，
两份实现迟早分叉——最危险的是「含糊预算不封顶」这类契约红线，复制之后改一处
漏一处，用户就会看到两套口径。抽出来后：

- 筛选口径（`candidate_frame`）只有一份
- 机型载荷（`models_payload`）只有一份
- 事实清单（`build_brief`）只有一份
- 追问上限与「用户弃权」判定只有一份

界面差异只剩「怎么显示」，不再是「怎么算」。

## 三轮对话的状态是**显式传参**，不是全局变量

Web 服务是无状态的，`handle()` 因此把 `history` / `asked` / `ask_rounds` 都作为
入参，并在 `Turn` 里原样回吐，由调用方保存后下一轮再传回来。Streamlit 侧则把
这些值存进 `session_state`。两边共用同一套推进规则，行为一致。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from bridge import (
    SCREEN_DEFAULT,
    STORAGE_DEFAULT,
    Bridged,
    bridge_contract,
)
from engine import rank, rank_decision_input
from catalog.contracts import RankingResult
from catalog.evidence import build_evidence
from needs_adapter import to_blue_needs
from needs_extraction import (
    FIELD_PATH_TO_ASK,
    STATUS_FAILED,
    extract_needs,
)
from providers.recommend import Brief, fallback_text, write_recommendation, explain_evidence

# 推荐语里提到的机型必须与用户看到的完全一致，否则「文案说 5 款、界面只列 3 款」
# 就是自相矛盾。所以两个界面共用同一个数量。
TOP_N = 5

# 追问上限。超过就不再问，直接把待确认项标在结果旁边——
# 否则用户一直答不到点子上就会陷入无限追问。
MAX_ASK_ROUNDS = 2

WEIGHT_LABELS = {
    'price': '价格更低',
    'weight': '机身更轻',
    'storage': '存储更大',
    'refresh': '刷新率更高',
    'screen': '屏幕接近偏好',
}

# 用户表示「不答了，你看着办」的短语。
# 刻意**不含「无所谓」**——抽取器用它来表达「不在意」这个偏好等级，
# 混进来会把「价格无所谓」误判成弃权。
_DECLINE_MARKERS = (
    '随便', '都行', '都可以', '你决定', '你看着办', '听你的', '不确定', '不知道',
)
_DECLINE_MAX_LEN = 10

_CN_NUMBERS = ('零', '一', '两', '三', '四', '五', '六', '七', '八', '九')

# 追问的收尾句。默认给 Streamlit（那边有确认表单）；Web 界面传自己的。
DEFAULT_ASK_FOOTER = '也可以在下方表单里修改；不想补充的条件可保持未启用，检查后确认。'


# --- 小工具 -----------------------------------------------------------------


def join_history(history: Sequence[str]) -> str:
    """把用户先后说的几句话拼成一段文本交给抽取器。

    带序号是为了让模型知道「这是先后补充」，prompt 里已说明后说的更准。
    """
    items = [text for text in history if (text or '').strip()]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    return '\n'.join(f'{index}. {text}' for index, text in enumerate(items, 1))


def is_declining(text: str) -> bool:
    """判断用户是否在说「我不想回答这个问题」。

    只在刚被追问过的那一轮生效（见 `handle`），且要求消息足够短，
    否则「随便看看两千块以内的」这种正常需求会被误判。
    """
    cleaned = re.sub(r'[\s，。、！？,.!?~～]+', '', text or '')
    return len(cleaned) <= _DECLINE_MAX_LEN and any(
        marker in cleaned for marker in _DECLINE_MARKERS
    )


def ask_text(
    pending: Sequence[Mapping[str, Any]],
    *,
    footer: str | None = DEFAULT_ASK_FOOTER,
) -> str:
    """把待确认项拼成追问话术。直接用契约里写好的 `question`。

    `footer` 是「接下来怎么办」的收尾句，默认是 Streamlit 的表单提示。
    Web 界面没有那张表单，传自己的文案进来——追问的问题本身仍然只有一份，
    两个界面不会问出不一样的话。
    """
    count = len(pending)
    if count == 1:
        head = '我先确认一件事：'
    else:
        # 中文语境里「2 件事」很突兀，用汉字数字
        word = _CN_NUMBERS[count] if count < len(_CN_NUMBERS) else str(count)
        head = f'我先确认{word}件事，免得推荐偏了：'
    lines = [head]
    for item in pending:
        lines.append(f'- {item["question"]}')
    if footer:
        lines.append('')
        lines.append(footer)
    return '\n'.join(lines)


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# --- 引擎侧：筛选、载荷、事实清单 -------------------------------------------


def candidate_frame(df: pd.DataFrame, bridged: Bridged) -> pd.DataFrame:
    """按 `Bridged` 里的条件筛选候选配置。

    白名单与排除名单是两件事：白名单非空表示「只看这些品牌」，
    排除名单表示「这些不要」。两者同时存在时先取交集再剔除。
    """
    candidates = df[df.price.between(*bridged.budget) & (df.storage >= bridged.storage)]
    if bridged.brands:
        candidates = candidates[candidates.brand.isin(bridged.brands)]
    if bridged.excluded_brands:
        candidates = candidates[~candidates.brand.isin(bridged.excluded_brands)]
    return candidates


def models_payload(
    result: pd.DataFrame, limit: int, budget_high: float | None
) -> list[dict[str, Any]]:
    """把排序结果转成可序列化的机型列表。

    先按配置算分，再为每个机型保留最高分配置——用户看的是「机型」，
    不是「配置」，同一机型出现五条会把清单挤爆。
    """
    if 'sku_id' in result and 'display_name' in result:
        models=result.drop_duplicates('model_id').head(limit)
        payload=[]
        for row in models.to_dict('records'):
            payload.append({'id':row['sku_id'],'name':row['display_name'],'brand':row.get('brand'),
                'config':str(row.get('ram_raw') or '缺失')+' / '+str(row.get('storage_raw') or '缺失'),
                'price':row.get('price_cny'),'score':row['score'],
                'reason':'当前可比候选集内相对排序；不代表绝对性能',
                'model_id':row['model_id'],'variant_id':row.get('variant_id'),'price_id':row.get('price_id')})
        return payload
    models = result.drop_duplicates('产品名称').head(limit)
    payload: list[dict[str, Any]] = []
    for _, row in models.iterrows():
        payload.append({
            'id': str(row['id']),
            'name': str(row['产品名称']),
            'brand': str(row['brand']),
            'config': str(row['配置']),
            'chip': str(row['处理器']),
            'price': float(row.price),
            'weight': float(row.weight),
            'screen': float(row.screen),
            'refresh': float(row.refresh),
            'storage': float(row.storage),
            'score': float(row.score),
            'reason': (f'比预算上限低 {budget_high - row.price:,.0f} 元'
                       if budget_high is not None else '未设置预算上限'),
        })
    return payload


def needs_summary(result: Mapping[str, Any], bridged: Bridged) -> str:
    """把抽取结果写成人能读的一段话，喂给推荐语写手。"""
    parts: list[str] = []
    if bridged.brands:
        parts.append('品牌只看：' + '、'.join(bridged.brands))
    if bridged.excluded_brands:
        parts.append('排除品牌：' + '、'.join(bridged.excluded_brands))
    if bridged.storage != STORAGE_DEFAULT:
        parts.append(f'最低存储 {bridged.storage}GB')
    stated = [
        f'{WEIGHT_LABELS.get(item["criterion"], item["criterion"])}={item["level"]}'
        for item in result.get('preferences') or []
        if item.get('level') != '未提及'
    ]
    if stated:
        parts.append('偏好：' + '，'.join(stated))
    else:
        parts.append('用户没有表达任何偏好')
    return '；'.join(parts) if parts else '（未提取到明确需求）'


def build_brief(
    result: Mapping[str, Any],
    bridged: Bridged,
    ranked: pd.DataFrame,
    *,
    top_n: int = TOP_N,
    budget_high: float | None = None,
    total_configs: int = 0,
) -> Brief:
    """组装交给大模型的**事实清单**。所有字段都来自引擎，不含模型推测。"""
    return Brief(
        models=models_payload(ranked, top_n, budget_high),
        needs_summary=needs_summary(result, bridged),
        pending=list(bridged.pending),
        unsupported=list(bridged.unsupported),
        candidate_configs=len(ranked),
        total_configs=total_configs,
        budget=list(bridged.budget) if budget_high is not None else None,
    )


def phones_payload(df: pd.DataFrame) -> list[dict[str, Any]]:
    """把配置表转成前端可用的 JSON 列表。

    只暴露页面确实要展示的字段：**没有电池容量、没有折叠形态**，因为原始
    数据表里就没有。前端不得据此编造「续航好」「折叠屏」这类结论。
    """
    payload: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        payload.append({
            'id': str(row['id']),
            'name': str(row['产品名称']),
            'brand': str(row['brand']),
            'config': str(row['配置']),
            'ram': str(row['运行内存']),
            'storage': float(row.storage),
            'weight': float(row.weight),
            'screen': float(row.screen),
            'refresh': float(row.refresh),
            'price': float(row.price),
            'chip': str(row['处理器']),
            'rear': str(row['后摄像头']),
            'front': str(row['前摄像头']),
            'resolution': str(row['分辨率']),
            'charging': str(row['充电功率']),
        })
    return payload


# --- 一轮对话 ---------------------------------------------------------------


@dataclass
class Turn:
    """一轮输入的处理结果。

    `stage` 只有三种，调用方据此决定界面表现：

    - `failed`  —— 抽取或契约校验失败。`error` 有原因，界面提示换种说法。
    - `clarify` —— 有未确认的硬约束，`questions` 是要问的问题，**先别出机型**。
    - `result`  —— 可以出结果。`models` 与 `advice` 已备好（可能为空）。

    `asked` / `ask_rounds` 必须由调用方保存并在下一轮原样传回，
    否则追问会重复或无限循环。
    """

    stage: str = 'result'
    error: str = ''
    questions: list[dict[str, Any]] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    ask_rounds: int = 0

    needs: dict[str, Any] = field(default_factory=dict)
    blue_needs: dict[str, Any] | None = None
    bridged: Bridged | None = None
    degraded: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    budget: list[float] | None = None
    target: float = SCREEN_DEFAULT
    weights: dict[str, float] = field(default_factory=dict)
    candidate_configs: int = 0
    total_configs: int = 0

    models: list[dict[str, Any]] = field(default_factory=list)
    advice: str = ''
    advice_error: str = ''
    ranking: RankingResult | None = None
    decision_input: dict | None = None
    evidence: dict | None = None

    @property
    def ok(self) -> bool:
        return self.stage == 'result' and not self.error

    @property
    def advice_from_llm(self) -> bool:
        return bool(self.advice) and not self.advice_error

    def to_dict(self) -> dict[str, Any]:
        """转成可直接 `json.dumps` 的 dict（tuple 变 list）。"""
        data = asdict(self)
        if self.bridged is not None:
            data['bridged'] = asdict(self.bridged)
        return _json_safe(data)


def _json_safe(value):
    import math
    if isinstance(value,pd.DataFrame):
        return _json_safe(value.astype(object).where(value.notna(),None).to_dict('records'))
    if isinstance(value,dict): return {k:_json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [_json_safe(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value


def _failed(result: Mapping[str, Any], error: str, **extra: Any) -> Turn:
    return Turn(stage='failed', error=error, needs=dict(result), **extra)


def handle(
    text: str,
    df: pd.DataFrame,
    *,
    history: Sequence[str] = (),
    asked: Iterable[str] = (),
    ask_rounds: int = 0,
    top_n: int = TOP_N,
    max_ask_rounds: int = MAX_ASK_ROUNDS,
    write: bool = True,
    writer: Any = None,
    classifier: Any = None,
    timeout: float | None = None,
    decision_builder: Any = None,
) -> Turn:
    """处理一轮用户输入，返回 `Turn`。**本函数不抛异常。**

    流程：拼历史 → 抽取 → 契约校验 → 翻译成引擎入参 → 追问判定 → 筛选排序 →
    组装事实清单 →（可选）生成推荐语。

    `write=False` 时跳过推荐语，只出机型——批量或离线场景不必为措辞付钱。
    """
    prompt = (text or '').strip()
    turns = [*(history or ()), prompt] if prompt else list(history or ())
    asked_set = {str(item) for item in (asked or ())}
    rounds = int(ask_rounds or 0)

    result = extract_needs(join_history(turns), classifier=classifier, timeout=timeout)

    if result.get('status') == STATUS_FAILED:
        reason = (result.get('meta') or {}).get('error') or '未知原因'
        return _failed(result, reason, asked=sorted(asked_set), ask_rounds=rounds)

    try:
        blue_needs = to_blue_needs(result)
    except ValueError as error:
        return _failed(result, str(error), asked=sorted(asked_set), ask_rounds=rounds)

    bridged, degraded = bridge_contract(
        result, known_brands=sorted(df.brand.unique())
    )

    # 追问决策：只问「没问过的」字段，且总轮数受 `max_ask_rounds` 限制。
    clarifications = [
        item for item in result.get('clarifications') or []
        if item.get('field') in FIELD_PATH_TO_ASK
    ]

    if rounds and is_declining(prompt):
        # 用户刚被问过、又明确表示不想回答：把本轮所有待确认项记成「已问过」，
        # 直接出结果。否则抽取器会把「随便」读成「屏幕随便」，又冒出新问题，
        # 用户就被困在追问循环里了。
        asked_set |= {item['field'] for item in clarifications}
        pending = []
    else:
        pending = [item for item in clarifications if item['field'] not in asked_set]

    common: dict[str, Any] = {
        'needs': dict(result),
        'blue_needs': blue_needs,
        'bridged': bridged,
        'degraded': list(degraded),
        'unsupported': list(bridged.unsupported),
        'total_configs': len(df),
    }

    if pending and rounds < max_ask_rounds:
        asked_set |= {item['field'] for item in pending}
        return Turn(
            stage='clarify',
            questions=[dict(item) for item in pending],
            asked=sorted(asked_set),
            ask_rounds=rounds + 1,
            pending=list(bridged.pending),
            **common,
        )

    # --- 出结果 ---
    candidates = candidate_frame(df, bridged) if decision_builder is None else pd.DataFrame()
    weights = dict(bridged.weights)
    target = bridged.target
    # 只有用户**明确说了**上限才拿来算「比预算低多少」；含糊预算没封顶，
    # 拿滑块量程上限去减会得到一个巨大的假数字。
    budget_high = _opt_float((result.get('budget') or {}).get('max'))

    turn = Turn(
        stage='result',
        asked=sorted(asked_set),
        ask_rounds=rounds,
        pending=list(bridged.pending),
        budget=list(bridged.budget),
        target=target,
        weights=weights,
        candidate_configs=len(candidates),
        **common,
    )

    if decision_builder is not None:
        try:
            di=decision_builder(dict(result),bridged)
            ranking=rank_decision_input(di,target_screen=target)
        except (ValueError,KeyError,TypeError,OverflowError):
            turn.stage='failed';turn.error='决策输入或排名计算失败，请检查数据与权重；不生成推荐成绩'
            return turn
        turn.ranking=ranking;turn.decision_input=di
        turn.candidate_configs=len(di['candidates'])
        if ranking.status!='ready':
            turn.stage=ranking.status;turn.error=ranking.reason;turn.advice=ranking.reason
            return turn
        turn.models=models_payload(ranking.ranked,top_n,budget_high)
        turn.evidence=build_evidence(di,ranking,dict(result),sku_ids=[m['id'] for m in turn.models])
        turn.advice,turn.advice_error=explain_evidence(turn.evidence)
        return turn

    if candidates.empty:
        turn.stage='no_candidates'
        brief = Brief(candidate_configs=0, total_configs=len(df))
        turn.advice = fallback_text(brief)
        turn.advice_error = '没有候选配置，未生成推荐语'
        return turn

    try:
        ranked = rank(candidates, weights, target)
    except ValueError as error:
        # 权重全为零、候选数据有缺失等。这类问题不该让整个服务 500。
        turn.error = str(error)
        turn.stage = 'failed'
        turn.advice = fallback_text(Brief(candidate_configs=len(candidates),
                                          total_configs=len(df)))
        turn.advice_error = str(error)
        return turn

    turn.models = models_payload(ranked, top_n, budget_high)
    brief = build_brief(
        result, bridged, ranked,
        top_n=top_n, budget_high=budget_high, total_configs=len(df),
    )

    if not write:
        turn.advice = fallback_text(brief)
        turn.advice_error = '未请求大模型推荐语'
        return turn

    advice, advice_error = write_recommendation(brief, writer=writer)
    if advice:
        turn.advice = advice
    else:
        turn.advice = fallback_text(brief)
        turn.advice_error = advice_error
    return turn


def handle_quiz(
    answers: Mapping[str, Any],
    df: pd.DataFrame,
    *,
    top_n: int = TOP_N,
    write: bool = True,
    writer: Any = None,
    classifier: Any = None,
    timeout: float | None = None,
) -> Turn:
    """快速问答的答卷 → `Turn`。

    **本函数只是 `handle()` 的一层薄封装**：把选项编号拼成一句中文需求，
    之后完全走同一条链路。这样做有两个好处：

    - 筛选、追问、排序、事实清单的口径与自由输入完全一致，不存在「问答模式
      另有一套算法」的可能；
    - 题库改文案不需要动这里，且问答结果与「用户自己把那句话打出来」等价。

    与自由输入的唯一差别：`ask_rounds` 传 `MAX_ASK_ROUNDS`（答题已用掉追问
    额度）。用户已经答完五道题，再拿「最多能接受到多少」去追问他，是在浪费
    他的耐心——预算题本身就是用来回答这个问题的。若答卷确实有缺口
    （如用户只答了部分题），缺口会出现在 `pending` 里如实告知，而不是变成
    新一轮追问。
    """
    from quiz import build_text  # 局部 import：pipeline 不依赖题库内容

    text = build_text(answers)
    return handle(
        text,
        df,
        ask_rounds=MAX_ASK_ROUNDS,
        top_n=top_n,
        write=write,
        writer=writer,
        classifier=classifier,
        timeout=timeout,
    )


__all__ = [
    'DEFAULT_ASK_FOOTER',
    'MAX_ASK_ROUNDS',
    'TOP_N',
    'WEIGHT_LABELS',
    'Bridged',
    'Turn',
    'ask_text',
    'build_brief',
    'candidate_frame',
    'handle',
    'handle_quiz',
    'is_declining',
    'join_history',
    'models_payload',
    'needs_summary',
    'phones_payload',
]
