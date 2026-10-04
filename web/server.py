"""知选 Web 服务：把算法内核暴露给静态前端。

## 为什么要有这一层

前端原型（`web/static/index.html`）本身是纯静态的，它页面里那句「AI 推荐」
其实是一段关键词正则，候选机型是 9 款硬编码的虚构机型。真正决定机型的应当是
本地引擎，而**密钥绝不能进浏览器**。

所以前端只把一句中文发给本服务，服务端完成整条链路：

    中文需求 → needs_extraction（大模型理解）→ bridge（契约适配）
             → engine.rank（TOPSIS 排序）→ 事实清单 → providers.recommend（大模型写解说）

机型全部由 `engine.rank` 产出，大模型只负责「理解」与「措辞」。这条分工与
Streamlit 界面完全一致——因为两边调的是同一个 `pipeline.handle`。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 静态页（`static/index.html`） |
| GET | `/api/meta` | 品牌、预算量程、存储档位、维度标签、数据规模、大模型状态 |
| GET | `/api/phones` | 全部真实配置（46 款机型 / 156 条配置） |
| GET | `/api/quiz` | 快速问答的题库 |
| POST | `/api/recommend` | 一句话 → 追问清单 或 机型清单 |
| POST | `/api/quiz/recommend` | 答卷 → 机型清单（不追问） |

`POST /api/recommend` 的请求体：

```json
{"text": "预算4000以内，想要轻一点的",
 "history": ["上一轮说的话"], "asked": ["budget.max"], "ask_rounds": 1,
 "top_n": 5, "write": true}
```

`history` / `asked` / `ask_rounds` 是**多轮对话的状态**，由前端保存并原样回传
（服务端无状态，不存 session）。`write=false` 可跳过推荐语，只出机型。

`POST /api/quiz/recommend` 的请求体：

```json
{"answers": {"budget": "b2", "scene": "photo", "carry": "light",
             "screen": "large", "storage": "s512"},
 "top_n": 5, "write": true}
```

选项编号由 `GET /api/quiz` 给出，服务端不认编号以外的值。答卷会经
`pipeline.handle_quiz` 拼成一句中文需求后走**与自由输入完全相同**的链路。

## 依赖

只用已装好的 `starlette` + `uvicorn`，**不新增依赖**，
`requirements.txt` 的 4 个运行时依赖保持不变。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from bridge import (  # noqa: E402
    BUDGET_DEFAULT,
    BUDGET_LIMIT,
    BUDGET_STEP,
    SCREEN_DEFAULT,
    SCREEN_RANGE,
    STORAGE_DEFAULT,
    STORAGE_OPTIONS,
)
from engine import load_data  # noqa: E402
from pipeline import (  # noqa: E402
    MAX_ASK_ROUNDS,
    TOP_N,
    WEIGHT_LABELS,
    ask_text,
    handle,
    handle_quiz,
    phones_payload,
)
from providers.env import load_dotenv  # noqa: E402
from providers.llm import llm_status  # noqa: E402
from quiz import (  # noqa: E402
    answer_labels,
    build_text,
    expected_unsupported,
    progress,
    quiz_payload,
)
from schemas import CRITERIA  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / 'static'

# 追问的收尾句。Streamlit 那边有确认表单，Web 这边只有按钮和输入框。
WEB_ASK_FOOTER = (
    '点上面的按钮，或直接在下面输入补充。'
    '不想回答就说一句「随便」，我会按现有条件先给结果。'
)

# 问答模式的收尾句：答题已经用掉追问额度，这里只剩「补充说明」的余地。
WEB_QUIZ_FOOTER = (
    '答题已经给出主要条件。想再补充别的，可以直接在下面输入。'
)

_FRAME: Any = None


def frame():
    """读取并缓存配置表。

    读 Excel 有几十毫秒开销，每个请求都读没必要；数据表在进程生命周期内不变。
    首次调用时顺带加载 `.env`——密钥只在这里进 `os.environ`，**不会**出现在
    任何响应里。
    """
    global _FRAME
    if _FRAME is None:
        load_dotenv()
        _FRAME = load_data()
    return _FRAME


def reset_frame() -> None:
    """丢掉缓存的数据表。测试用；生产环境不需要。"""
    global _FRAME
    _FRAME = None


# --- 请求参数 ---------------------------------------------------------------


def _str_list(value: Any) -> list[str]:
    """把请求体里的字符串数组清洗成 `list[str]`。

    前端传什么都有可能（`null`、字符串、对象数组），这里一律收敛，
    绝不让脏数据进到管线里。
    """
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Sequence):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


# --- 路由 -------------------------------------------------------------------


async def api_meta(request) -> JSONResponse:
    """界面初始化需要的全部常量。**不含任何密钥。**"""
    df = frame()
    ready, message = llm_status()
    return JSONResponse({
        'brands': sorted(str(brand) for brand in df.brand.unique()),
        'budget': {
            'limit': list(BUDGET_LIMIT),
            'step': BUDGET_STEP,
            'default': list(BUDGET_DEFAULT),
        },
        'storage': {'options': list(STORAGE_OPTIONS), 'default': STORAGE_DEFAULT},
        'screen': {'range': list(SCREEN_RANGE), 'default': SCREEN_DEFAULT},
        'criteria': list(CRITERIA),
        'weight_labels': dict(WEIGHT_LABELS),
        'totals': {
            'models': int(df['产品名称'].nunique()),
            'configs': int(len(df)),
        },
        'top_n': TOP_N,
        'max_ask_rounds': MAX_ASK_ROUNDS,
        'llm': {'ready': ready, 'message': message},
    })


async def api_phones(request) -> JSONResponse:
    """全部真实配置。前端只展示，不做任何推断。"""
    return JSONResponse({'phones': phones_payload(frame())})


async def api_recommend(request) -> JSONResponse:
    """一句话需求 → `Turn`。

    失败也返回 200：`stage` 字段已经表达了结果，前端按 `stage` 分支即可。
    只有请求体本身不合法（不是 JSON、`text` 为空）才用 4xx——那是调用方的错，
    不是「没理解需求」。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 任何解析失败都当请求体不合法
        return JSONResponse({'error': '请求体必须是合法 JSON'}, status_code=400)
    if not isinstance(body, Mapping):
        return JSONResponse({'error': '请求体必须是 JSON 对象'}, status_code=400)

    text = body.get('text')
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({'error': 'text 不能为空'}, status_code=400)

    # 分类器可以注入：`app.state.classifier` 一旦设上，就跳过默认的
    # `providers.llm.LLMClassifier`。测试与离线演示用它跑 `MockClassifier`，
    # 这样跑测试**不会产生任何线上调用费用**。
    classifier = getattr(request.app.state, 'classifier', None)

    turn = handle(
        text,
        frame(),
        history=_str_list(body.get('history')),
        asked=_str_list(body.get('asked')),
        ask_rounds=_int(body.get('ask_rounds'), 0),
        top_n=_int(body.get('top_n'), TOP_N),
        write=body.get('write') is not False,
        classifier=classifier,
    )

    payload = turn.to_dict()
    # 追问话术在服务端拼好，两个界面（Streamlit / Web）的措辞就永远一致；
    # 只有收尾句按界面替换——Web 没有 Streamlit 那张确认表单。
    payload['ask_text'] = (
        ask_text(turn.questions, footer=WEB_ASK_FOOTER) if turn.questions else ''
    )
    # `ok` / `advice_from_llm` 是 `Turn` 的 property，`asdict()` 不会带上，
    # 显式补进响应体，前端不必自己从 `stage` / `advice_error` 反推。
    payload['ok'] = turn.ok
    payload['advice_from_llm'] = turn.advice_from_llm
    payload['llm_ready'] = llm_status()[0]
    return JSONResponse(payload)


async def api_quiz(request) -> JSONResponse:
    """快速问答的题库。前端只渲染，题目**不在页面里硬编码**。"""
    return JSONResponse(quiz_payload())


async def api_quiz_recommend(request) -> JSONResponse:
    """答卷 → `Turn`。

    请求体：`{"answers": {"budget": "b2", "scene": "photo", ...}, "write": true}`

    走 `pipeline.handle_quiz`，与自由输入**同一条链路**。响应里额外带上
    `answers_text`（拼出的需求原话）与 `answer_labels`（回显），让用户能看见
    「你的选择被怎么理解」——问答模式最怕的就是点了一堆按钮却不知道算进去了
    什么。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({'error': '请求体必须是合法 JSON'}, status_code=400)
    if not isinstance(body, Mapping):
        return JSONResponse({'error': '请求体必须是 JSON 对象'}, status_code=400)

    answers = body.get('answers')
    if not isinstance(answers, Mapping):
        return JSONResponse({'error': 'answers 必须是 JSON 对象'}, status_code=400)

    # 只保留值是字符串或字符串数组的项，其余一律丢弃——脏数据不进管线。
    clean: dict[str, Any] = {}
    for key, value in answers.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            clean[key] = value
        elif isinstance(value, Sequence):
            items = [item for item in value if isinstance(item, str)]
            if items:
                clean[key] = items

    text = build_text(clean)
    if not text:
        return JSONResponse({'error': '还没有选出任何答案'}, status_code=400)

    classifier = getattr(request.app.state, 'classifier', None)
    turn = handle_quiz(
        clean,
        frame(),
        top_n=_int(body.get('top_n'), TOP_N),
        write=body.get('write') is not False,
        classifier=classifier,
    )

    payload = turn.to_dict()
    payload['ok'] = turn.ok
    payload['advice_from_llm'] = turn.advice_from_llm
    payload['llm_ready'] = llm_status()[0]
    # 问答模式不追问（答题已用掉额度），`ask_text` 保持空串以免前端误渲染。
    payload['ask_text'] = ''
    payload['answers_text'] = text
    payload['answer_labels'] = answer_labels(clean)
    payload['progress'] = progress(clean)
    # 提前告知「这些场景本次没有数据依据」。真正的判定仍以抽取结果为准，
    # 此处只用于前端在出结果前就能给出提示。
    payload['expected_unsupported'] = expected_unsupported(clean)
    return JSONResponse(payload)


routes = [
    Route('/api/meta', api_meta, methods=['GET']),
    Route('/api/phones', api_phones, methods=['GET']),
    Route('/api/quiz', api_quiz, methods=['GET']),
    Route('/api/recommend', api_recommend, methods=['POST']),
    Route('/api/quiz/recommend', api_quiz_recommend, methods=['POST']),
    Mount('/', app=StaticFiles(directory=str(STATIC_DIR), html=True), name='static'),
]

app = Starlette(routes=routes, debug=False)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', '8000')),
        log_level=os.environ.get('LOG_LEVEL', 'info'),
    )


if __name__ == '__main__':
    main()
