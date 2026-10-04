"""快速问答：「你拿这台手机干什么」→ 三选一 → 直接出机型。

## 设计约束（这份题库为什么长这样）

内核能算的只有 5 个维度：价格、重量、存储、刷新率、屏幕尺寸。它**明确不能算**
影像、续航、真实游戏性能、分辨率、机身尺寸手感、信号质量、处理器性能——
这些在 `providers/jev.UNSUPPORTED_RULES` 里，命中时会进 `unsupported_needs`，
由界面如实告知用户，**不得用别的指标替代**。

所以每道题的选项都必须做两件事：

1. **用生活场景描述**，让不懂参数的用户也能选出来（「经常出差在外」比
   「刷新率要 120Hz」好选太多）；
2. **携带一个内核真能算的维度倾向**，否则这道题对结果毫无影响，
   只会让用户白答。

「拍照」「打游戏」这类场景照问——它们的价值是让用户觉得被听见，
同时**如实进 `unsupported_needs`**，结果区会明说「这几项本次没有数据依据」。
这不是缺点，是这套系统的诚实之处。

## 措辞是实测调出来的，别凭感觉改

`tests/test_quiz.py` 会用 `MockClassifier` 断言每道选项解析出的维度与等级，
改文案前先跑测试。已知的坑：

- 「轻一点」→ weight 只到「一般」；要「越轻越好」才到「重要」。
- 「预算不是问题」不封顶，会触发追问，不能当「不限预算」用。
- 屏幕题的 evidence 必须取自原文（`6.8 英寸` 含空格），详见
  `providers/jev._screen_evidence`。

## 为什么放在 `pipeline.py` 外面

题库是**产品内容**，不是算法。它的产出只是一段中文需求文本，之后完全走
`pipeline.handle` 同一条链路——两个界面共用管线的约束在这里同样成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# 一次问几道。太多用户会不耐烦，太少区分度不够。
MAX_QUESTIONS = 5


@dataclass(frozen=True)
class Option:
    """一个选项。

    `say` 是**拼进需求文本的原话**，必须是完整自然句——它会原样喂给
    `extract_needs`，措辞直接决定抽取结果。`note` 是给用户看的补充说明，
    不参与组句。
    """

    key: str
    label: str
    desc: str
    say: str = ""
    note: str = ""


@dataclass(frozen=True)
class Question:
    key: str
    title: str
    hint: str
    options: tuple[Option, ...]
    # 这道题主要落在哪个引擎维度上（只用于展示与测试断言，不参与计算）
    criterion: str = ""


# --- 题库 -------------------------------------------------------------------

QUESTIONS: tuple[Question, ...] = (
    Question(
        key="budget",
        title="预算大概多少？",
        hint="这是最影响候选范围的一项。",
        criterion="price",
        options=(
            Option("b1", "3000 元以内", "够用就好，不追新",
                   say="预算 3000 以内"),
            Option("b2", "3000 到 4500 元", "主流区间，选择最多",
                   say="预算 3000 到 4500"),
            Option("b3", "4500 元以上", "愿意为更好的配置多花一点",
                   say="预算 4500 到 6500"),
        ),
    ),
    Question(
        key="scene",
        title="平时主要拿它干什么？",
        hint="选最接近的一项就行。",
        options=(
            Option("daily", "刷视频、看新闻、聊天", "日常够用就行",
                   say="就是刷视频看新闻，日常够用就行"),
            Option("photo", "拍照、出门玩要拍很多照片", "照片视频很占地方",
                   say="喜欢拍照，出门玩要拍很多照片，存储越大越好"),
            Option("game", "打游戏，性能要跟得上", "画面要跟得上手",
                   say="主要打游戏，性能要跟得上，也想要高刷"),
        ),
    ),
    Question(
        key="carry",
        title="出门的时候，手机怎么带着？",
        hint="重量对手感的影响比参数表上看起来大。",
        criterion="weight",
        options=(
            Option("light", "经常揣兜里、随手拿", "越轻越好，别坠手",
                   say="经常随身带着，越轻越好"),
            Option("normal", "放包里的时候多", "重量别太夸张就行",
                   say="平时放包里多，重量别太重，轻一些好"),
            Option("careless", "不太在意重量", "重一点也能接受",
                   say="重量不太在意"),
        ),
    ),
    Question(
        key="screen",
        title="喜欢多大的屏幕？",
        hint="按平时看视频、读字的习惯挑。",
        criterion="screen",
        options=(
            Option("small", "小一点，握着顺手", "差不多 6.1 英寸",
                   say="想要 6.1 英寸左右的小屏，偏小一点更好"),
            Option("middle", "中等，拿在手里正好", "差不多 6.5 英寸",
                   say="想要 6.5 英寸左右的"),
            Option("large", "大一点，看视频舒服", "差不多 6.8 英寸",
                   say="想要 6.8 英寸左右的大屏"),
        ),
    ),
    Question(
        key="storage",
        title="要装多少东西？",
        hint="照片、视频、聊天记录都很占地方。",
        criterion="storage",
        options=(
            Option("s256", "照片聊天为主", "256GB 起就够",
                   say="256G 起步就行"),
            Option("s512", "会存不少视频", "512GB 起更稳妥",
                   say="存储越大越好，512G 起步就行"),
            Option("s1t", "拍视频、装大游戏", "1TB 才不慌",
                   say="要 1TB 的，存储最重要"),
        ),
    ),
)


# 场景题里那些「问了但算不了」的需求。用于向用户解释，不参与筛选。
# 键是选项 key，值是会进 `unsupported_needs` 的维度名（与内核口径一致）。
SCENE_UNSUPPORTED: dict[str, tuple[str, ...]] = {
    "photo": ("影像",),
    "game": ("真实游戏性能",),
}


# --- 选项索引 ---------------------------------------------------------------

OPTIONS: dict[tuple[str, str], Option] = {
    (question.key, option.key): option
    for question in QUESTIONS
    for option in question.options
}


def question_for(key: str) -> Question | None:
    for question in QUESTIONS:
        if question.key == key:
            return question
    return None


# --- 组句 -------------------------------------------------------------------

def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def answer_says(answers: Mapping[str, Any]) -> list[str]:
    """把答案表转成有序的「原话」列表。

    顺序按 `QUESTIONS` 的定义走，与用户点击顺序无关——同样的答案组合必须
    得到**同一句**需求，否则同一份答卷两次提交会抽出不同结果。
    未知的题号 / 选项号一律忽略（前端可能送来过期数据）。
    """
    says: list[str] = []
    seen_questions: set[str] = set()
    for question in QUESTIONS:
        if question.key in seen_questions:
            continue
        seen_questions.add(question.key)
        raw = answers.get(question.key)
        if raw is None:
            continue
        # 同一题允许传字符串（单个选项）或数组（多选，目前题库都是单选）
        keys = raw if isinstance(raw, (list, tuple)) else [raw]
        for key in keys:
            option = OPTIONS.get((question.key, _clean(key)))
            if option is not None and option.say:
                says.append(option.say)
    return says


def build_text(answers: Mapping[str, Any]) -> str:
    """答案表 → 一句中文需求。

    用「，」连接而不是换行：`pipeline.handle` 收到的是单轮输入，
    多行会被 `join_history` 当成多轮对话处理。
    """
    says = answer_says(answers)
    if not says:
        return ""
    return "，".join(says) + "。"


def answer_labels(answers: Mapping[str, Any]) -> list[dict[str, str]]:
    """回显给用户的「你的选择」清单。"""
    rows: list[dict[str, str]] = []
    for question in QUESTIONS:
        raw = answers.get(question.key)
        if raw is None:
            continue
        keys = raw if isinstance(raw, (list, tuple)) else [raw]
        for key in keys:
            option = OPTIONS.get((question.key, _clean(key)))
            if option is not None:
                rows.append({
                    "question": question.title,
                    "label": option.label,
                    "desc": option.desc,
                })
    return rows


def expected_unsupported(answers: Mapping[str, Any]) -> list[str]:
    """这次答卷会命中哪些「内核算不了」的需求。

    只用于前端提前提示；真正的判定仍在 `needs_extraction` 里做，
    不在这里断言结果——两处口径可能因为模型差异而不同，
    以抽取结果为准。
    """
    needs: list[str] = []
    for question in QUESTIONS:
        option_key = _clean(answers.get(question.key))
        for need in SCENE_UNSUPPORTED.get(option_key, ()):
            if need not in needs:
                needs.append(need)
    return needs


def progress(answers: Mapping[str, Any]) -> dict[str, int]:
    """答题进度，用于前端显示。只统计**已答**的必答题。"""
    answered = sum(
        1 for question in QUESTIONS
        if _clean(answers.get(question.key))
        or isinstance(answers.get(question.key), (list, tuple))
    )
    return {"answered": answered, "total": len(QUESTIONS)}


def quiz_payload() -> dict[str, Any]:
    """题库的 JSON 表示。前端只渲染，不内置任何题目。"""
    return {
        "questions": [
            {
                "key": question.key,
                "title": question.title,
                "hint": question.hint,
                "criterion": question.criterion,
                "options": [
                    {
                        "key": option.key,
                        "label": option.label,
                        "desc": option.desc,
                        "note": option.note,
                    }
                    for option in question.options
                ],
            }
            for question in QUESTIONS
        ],
        "max_questions": MAX_QUESTIONS,
    }


__all__ = [
    "MAX_QUESTIONS",
    "OPTIONS",
    "QUESTIONS",
    "SCENE_UNSUPPORTED",
    "Option",
    "Question",
    "answer_labels",
    "answer_says",
    "build_text",
    "expected_unsupported",
    "progress",
    "question_for",
    "quiz_payload",
]
