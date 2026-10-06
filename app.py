"""知选 · 手机推荐实验室。

两条入口，共用同一份数据与同一套TOPSIS算法：

1. **对话框**（主入口）：用中文说需求 → 大模型抽取 → 用户确认 → 取舍追问 → 引擎算。
   信息不全时先追问（最多 `MAX_ASK_ROUNDS` 轮），追问话术来自
   `needs_extraction` 的 `clarifications`。
2. **侧边栏滑块**（手动模式）：可独立调整；确认与追问模式使用确认表单的值。

**机型永远由引擎算，不由大模型报。** 大模型的职责只有两个：
理解需求（`needs_extraction`）和把引擎结果写成一段话（`providers.recommend`）。
界面始终把引擎的机型卡片渲染在推荐语下方——以卡片为准。
"""

import os

import streamlit as st

from bridge import (
    BUDGET_DEFAULT,
    BUDGET_LIMIT,
    BUDGET_STEP,
    SCREEN_DEFAULT,
    SCREEN_RANGE,
    STORAGE_DEFAULT,
    STORAGE_OPTIONS,
    bridge_contract,
)
from engine import load_data, rank
from needs_extraction import (
    FIELD_PATH_TO_ASK,
    STATUS_FAILED,
    extract_needs,
)
from providers.env import load_dotenv
from providers.recommend import Brief, fallback_text, write_recommendation
from schemas import CRITERIA
from needs_adapter import to_blue_needs
from preferences import filter_candidates
from preference_panel import render_preference_panel
from pipeline import (
    MAX_ASK_ROUNDS,
    TOP_N,
    WEIGHT_LABELS,
    Bridged,
    ask_text,
    candidate_frame,
    is_declining,
    join_history,
    models_payload,
)

st.set_page_config(page_title='知选 · 手机推荐实验室',page_icon='📱',layout='wide')
st.markdown('''<style>
.stApp {background:#f7f9fc} .block-container {max-width:1280px;padding-top:2.5rem}
h1,h2,h3 {color:#16283f} [data-testid="stMetric"] {background:white;border:1px solid #e3e9f1;border-radius:12px;padding:16px}
[data-testid="stSidebar"] {background:#edf2f8} .stButton>button {border-radius:8px}
</style>''',unsafe_allow_html=True)

# 侧边栏里所有带 key 的控件。对话抽出的条件写这些键，清空时删这些键。
# 筛选控件加 filter_ 前缀：权重滑块的 key 直接用维度名（CRITERIA），
# 两者若不隔离，`storage` 会同时被下拉框和滑块占用而报重复 key。
FILTER_KEYS = {
    'budget': 'filter_budget',
    'brands': 'filter_brands',
    'storage': 'filter_storage',
    'target': 'filter_target',
}
WIDGET_KEYS = (*FILTER_KEYS.values(), *CRITERIA)
# 权重滑块默认值。只属于界面——`pipeline` 不关心默认值长什么样。
WEIGHT_DEFAULTS = {'price':35,'weight':20,'storage':20,'refresh':15,'screen':10}

CHAT_STATE_KEYS = (
    'chat', 'need_history', 'asked', 'ask_rounds',
    'needs', 'bridged', 'degraded', 'parse_error',
)


@st.cache_data
def data(): return load_data()


def _prepare_llm_env() -> None:
    """把配置搬进环境变量。`providers.llm` 只读环境变量，密钥不落盘。

    优先级：**真实环境变量 > `.env` > `st.secrets`**。

    - `.env` 是本地开发路径。注意：在本函数之前，`.env.example` 承诺的
      「复制为 .env 就能用」其实**从未生效**——没有任何代码读它。
    - `st.secrets` 是 Streamlit 云端部署路径。
    - 三者都是「只填不覆盖」，所以临时 `export` 一个值永远压得住另外两个。
    """
    load_dotenv()
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY'):
        try:
            value = secrets[key]
        except Exception:
            continue
        if value and not os.environ.get(key):
            os.environ[key] = str(value)


@st.cache_resource(show_spinner=False)
def llm_status():
    """探测大模型是否可用，返回 (是否可用, 原因)。

    未配置时界面退回纯手动滑块模式，而不是报错崩溃。

    判定逻辑与 Web 服务共用 `providers.llm.llm_status`——**只检查配置，
    不发真实请求**，界面加载不产生调用费用。真调一次请用
    `python tools/check_llm.py`。
    """
    _prepare_llm_env()
    from providers.env import llm_enabled
    if not llm_enabled():
        return False, '大模型默认关闭，手动推荐可用；启用真实调用需另行确认预算和网关限额'
    from providers.llm import llm_status as probe
    return probe()


# --- 对话链路 ---------------------------------------------------------------
#
# 抽取、筛选、机型载荷、追问话术这些「怎么算」的部分都在 `pipeline` 里，
# 与 Web 服务共用同一份实现。本段只负责**界面状态**：把结果写进
# `session_state`，让侧边栏控件和结果区拿到同一份数据。


def handle_turn(prompt: str, df) -> None:
    """处理一轮用户输入。

    **必须在侧边栏控件创建之前调用**：本函数会写 `FILTER_KEYS` 与权重滑块的
    session_state，控件一旦实例化就不能再赋值。
    """
    state = st.session_state
    text = prompt.strip()
    state.setdefault('chat', []).append({'role': 'user', 'text': text})
    history = state.setdefault('need_history', [])
    history.append(text)

    state.pop('active_ranking_ids', None)
    result = extract_needs(join_history(history))
    state.needs = result

    if result['status'] == STATUS_FAILED:
        reason = (result.get('meta') or {}).get('error') or '未知原因'
        state.parse_error = reason
        state.pop('extracted_needs', None)
        state.blue_session = None
        state.preference_mode = '手动调整'
        state.chat.append({
            'role': 'assistant',
            'kind': 'error',
            'text': f'这次没能理解你的需求（{reason}）。\n\n'
                    f'换个说法再试一次，或者直接用左侧的滑块手动调条件。',
        })
        return

    try:
        blue_needs = to_blue_needs(result)
    except ValueError as error:
        state.parse_error = str(error)
        state.pop('extracted_needs', None)
        state.blue_session = None
        state.preference_mode = '手动调整'
        state.chat.append({'role': 'assistant', 'kind': 'error',
                           'text': '需求输出未通过接口检查，请重新输入或使用手动模式。'})
        return

    bridged, degraded = bridge_contract(result, known_brands=sorted(df.brand.unique()))
    state.bridged = bridged
    state.degraded = degraded
    state.parse_error = ''
    state.extracted_needs = blue_needs
    state.blue_session = None
    state.pop('blue_model_fingerprint', None)
    state.preference_mode = '需求确认与追问'

    # 写控件键。此时侧边栏控件尚未创建，赋值合法；重跑后它们以新值初始化。
    state[FILTER_KEYS['budget']] = bridged.budget
    state[FILTER_KEYS['brands']] = bridged.brands
    state[FILTER_KEYS['storage']] = bridged.storage
    state[FILTER_KEYS['target']] = bridged.target
    for key, value in bridged.weights.items():
        state[key] = value

    # 追问决策：只问「没问过的」字段，且总轮数受 MAX_ASK_ROUNDS 限制。
    asked = set(state.get('asked') or ())
    rounds = int(state.get('ask_rounds') or 0)
    clarifications = [
        item for item in result.get('clarifications') or []
        if item.get('field') in FIELD_PATH_TO_ASK
    ]

    if rounds and is_declining(text):
        # 用户刚被问过、又明确表示不想回答：把本轮所有待确认项记成「已问过」，
        # 直接出结果。否则抽取器会把「随便」读成「屏幕随便」，又冒出新问题，
        # 用户就被困在追问循环里了。
        state.asked = sorted(asked | {item['field'] for item in clarifications})
        pending = []
    else:
        pending = [item for item in clarifications if item['field'] not in asked]

    if pending and rounds < MAX_ASK_ROUNDS:
        state.ask_rounds = rounds + 1
        state.asked = sorted(asked | {item['field'] for item in pending})
        state.chat.append({
            'role': 'assistant',
            'kind': 'ask',
            'text': ask_text(pending),
        })
        return

    state.chat.append({
        'role': 'assistant', 'kind': 'confirm',
        'text': '需求已整理好。请检查下面的条件和偏好，点击“确认需求并生成推荐”。'
                '之后可用两道取舍题进一步调整推荐。',
    })


def _render_model_list(models: list[dict]) -> None:
    """对话里的机型清单：紧凑、可扫读。完整卡片留在下方结果区，避免重复。"""
    lines = []
    for index, model in enumerate(models, 1):
        lines.append(
            f'{index}. **{model["name"]}**　{model["config"]}　'
            f'¥{model["price"]:,.0f}　'
            f'<span style="color:#6b7a8f">匹配度 {model["score"] * 100:.1f}%</span>'
        )
    st.markdown('  \n'.join(lines), unsafe_allow_html=True)
    st.caption(
        '匹配度是本次候选集内的相对得分，不是质量评分。'
        '完整参数与对比见下方「为你推荐」。'
    )


def render_chat() -> None:
    for message in st.session_state.get('chat') or []:
        role = message.get('role', 'assistant')
        with st.chat_message(role, avatar='🧑' if role == 'user' else '📱'):
            st.markdown(message.get('text', ''))
            if message.get('models'):
                st.caption('这是生成这条消息时的推荐记录；当前权重对应的最新排序见下方“为你推荐”。')
                _render_model_list(message['models'])
                if not message.get('advice_from_llm'):
                    st.caption(
                        '（推荐语未由大模型生成，已改用模板文案：'
                        f'{message.get("advice_error") or "未接入"}）'
                    )


# --- 页面 -------------------------------------------------------------------

st.caption('知选  /  PERSONAL PHONE LAB')
st.title('找到更适合你的手机')

try:
    df = data()
except Exception as error:
    st.error(f'数据读取失败：{error}')
    st.stop()

ALL_BRANDS = sorted(df.brand.unique())
llm_ready, llm_message = llm_status()

# 对话输入必须在这里处理：它会写 session_state 里的控件键，
# 而侧边栏控件一旦创建就不能再赋值。
prompt = st.chat_input(
    '说说你的需求，例如：预算三千左右，想要轻一点的，主要看价格',
    key='chat_prompt',
    disabled=not llm_ready,
)
if prompt:
    with st.spinner('正在理解你的需求…'):
        handle_turn(prompt, df)

with st.sidebar:
    # 清空动作必须在任何控件创建之前处理，否则改不动已实例化的控件
    if st.session_state.pop('_reset', False):
        blue_keys = [key for key in st.session_state if str(key).startswith(('blue_', 'level_', 'FormSubmitter:'))]
        for key in (*CHAT_STATE_KEYS, 'chat_prompt', *WIDGET_KEYS, *blue_keys,
                    'extracted_needs', 'active_ranking_ids', 'preference_mode'):
            st.session_state.pop(key, None)

    st.header('你的选购需求')

    if not llm_ready:
        st.caption('未配置大模型，当前为手动模式。滑块可以照常用。')
        with st.expander('如何接入真实 API'):
            st.write('**方式一：`.env` 文件（推荐，本地开发）**')
            st.code(
                'cp .env.example .env\n'
                '# 然后编辑 .env，填入真实值：\n'
                'LLM_BASE_URL=https://api.deepseek.com/v1\n'
                'LLM_MODEL=deepseek-chat\n'
                'LLM_API_KEY=sk-...',
                language='bash',
            )
            st.write('**方式二：环境变量**')
            st.code(
                'export LLM_BASE_URL=https://api.deepseek.com/v1\n'
                'export LLM_MODEL=deepseek-chat\n'
                'export LLM_API_KEY=sk-...',
                language='bash',
            )
            st.caption(
                '优先级：真实环境变量 > `.env` > `.streamlit/secrets.toml`。'
                '`.env` 与 `secrets.toml` 都在 .gitignore 里，不会被提交。'
            )
            st.caption(
                '配好后用 `python tools/check_llm.py` 体检一次——它会真调一次线上服务，'
                '把原始回复、契约结构和耗时都打出来。'
            )
            st.caption(f'当前状态：{llm_message}')
    else:
        st.caption(llm_message)
        st.caption('想确认真的接通了，跑 `python tools/check_llm.py` 体检一次。')

    if st.button('清空对话与条件', use_container_width=True):
        st.session_state['_reset'] = True
        st.rerun()

    if st.session_state.get('parse_error'):
        st.error(f'解析失败：{st.session_state["parse_error"]}')

    mode = st.radio('设置方式', ['手动调整', '需求确认与追问'], key='preference_mode')
    if mode == '手动调整':
        with st.expander('筛选条件', expanded=True):
            budget = st.slider('预算范围（元）', *BUDGET_LIMIT, BUDGET_DEFAULT, BUDGET_STEP, key=FILTER_KEYS['budget'])
            brands = st.multiselect('品牌（留空表示全部）', ALL_BRANDS, key=FILTER_KEYS['brands'])
            storage = st.selectbox('最低存储容量（GB）', STORAGE_OPTIONS, index=STORAGE_OPTIONS.index(STORAGE_DEFAULT), key=FILTER_KEYS['storage'])
            target = st.slider('偏好的屏幕尺寸（英寸）', *SCREEN_RANGE, SCREEN_DEFAULT, .1, key=FILTER_KEYS['target'])

        with st.expander('重要程度权重', expanded=False):
            st.caption('0 表示不计入评分；设置会自动归一化为权重。')
            weights = {k: st.slider(WEIGHT_LABELS[k], 0, 100, WEIGHT_DEFAULTS[k], 5, key=k) for k in CRITERIA}
            st.caption('当前使用手动滑块权重。切换到需求确认与追问可使用等级映射与取舍调整。')
# 主区域
if not (st.session_state.get('chat') or []):
    st.write(
        '在下面的输入框里用一句话说需求，系统抽取偏好后请先确认，再给出机型；'
        '也可以直接用左侧滑块手动调整。'
    )
    if not llm_ready:
        st.info('对话框需要接入大模型才能用。当前可以先用左侧滑块手动筛选。')

render_chat()

blue_session = None
if mode == '需求确认与追问':
    st.session_state.pop('active_ranking_ids', None)
    blue_session = render_preference_panel(df)
    confirmed_needs = blue_session['needs']
    weights = blue_session['current_weights']
    target = confirmed_needs['target_screen_inches'] or SCREEN_DEFAULT
    # target为空时屏幕权重为0，这个占位值不参与评分。
    hard = confirmed_needs['hard_constraints']
    budget_upper = hard['max_price']
    budget = (hard.get('min_price') or 0, budget_upper)
else:
    budget_upper = budget[1]

bridged = st.session_state.get('bridged')
if bridged is not None and mode == '手动调整':
    with st.container(border=True):
        st.subheader('我对你需求的理解')
        left, right = st.columns(2)
        with left:
            st.write(f"预算　¥{budget[0]:,} – ¥{budget[1]:,}")
            st.write(f"最低存储　{storage}GB　｜　屏幕偏好　{target:g} 英寸")
            st.write(f"品牌白名单　{'、'.join(brands) if brands else '不限'}")
            st.write(f"排除品牌　{'、'.join(bridged.excluded_brands) if bridged.excluded_brands else '无'}")
        with right:
            st.write('权重　' + '　'.join(f'{WEIGHT_LABELS[k]} {int(weights[k])}' for k in CRITERIA))
        if st.session_state.get('degraded'):
            st.warning(
                '模型给的条件里有无法采用的部分，已忽略：'
                + '；'.join(st.session_state['degraded'])
            )
        if bridged.pending:
            st.warning('下面这些我没有可靠依据，请你确认后再看推荐：' + '、'.join(bridged.pending))
        if bridged.unsupported:
            st.error('这些需求现有数据覆盖不到，不会生成结论，也不会用别的指标替代：' + '、'.join(bridged.unsupported))
        with st.expander('这些条件是怎么来的'):
            for note in bridged.notes:
                st.write('· ' + note)
            st.caption('说明：偏好等级「一般」与「未提及」在当前映射表里都取 1，'
                       '所以「轻一点」这类弱表达不会改变权重。要拉开差距请说「越轻越好」。')

if blue_session is not None:
    candidates = filter_candidates(df, blue_session['needs'])
else:
    # 手动模式的筛选口径与对话链路共用 `candidate_frame`：白名单与排除名单
    # 的处理规则只有一份，两条路径不会分叉。
    candidates = candidate_frame(df, Bridged(
        budget=tuple(budget),
        brands=list(brands),
        excluded_brands=list(bridged.excluded_brands) if bridged is not None else [],
        storage=storage,
        target=target,
        weights=dict(weights),
    ))

cols = st.columns(4)
for col, title, value in zip(cols, ['数据机型', '配置记录', '筛选后机型', '筛选后配置'], [df['产品名称'].nunique(), len(df), candidates['产品名称'].nunique(), len(candidates)]):
    col.metric(title, value)

st.info('价格均为数据表记录价，未核实当前售价和库存。处理器、摄像头与充电参数仅展示，不参与评分；本原型不评价游戏性能或拍照质量。')

if candidates.empty:
    st.warning('当前条件下没有候选，请放宽预算、品牌或最低存储要求。')
    st.stop()
if sum(weights.values()) == 0:
    st.warning('请至少将一项重要程度设为大于 0。')
    st.stop()

result = rank(candidates, weights, target)
st.session_state['active_ranking_ids'] = result.id.tolist()
models = result.drop_duplicates('产品名称').head(TOP_N)

if blue_session is not None:
    with st.expander('追问前后的推荐对比'):
        initial_rank = rank(candidates, blue_session['initial']['weights'], target)
        left, right = st.columns(2)
        fields = ['产品名称', '配置', 'price', 'weight', 'score']
        with left:
            st.write('确认后的初始推荐')
            st.dataframe(initial_rank.drop_duplicates('产品名称').head(TOP_N)[fields], hide_index=True)
        with right:
            st.write('按当前权重推荐')
            st.dataframe(models[fields], hide_index=True)
    # 推荐语只在显式点击时请求，且只使用本轮实际排序，避免重跑重复调用。
    if st.button('生成当前推荐说明'):
        current_models = models_payload(result, TOP_N, budget_upper)
        brief = Brief(
            models=current_models,
            needs_summary='确认后的偏好：' + str(blue_session['needs']['preferences']),
            unsupported=blue_session['needs']['unsupported_needs'],
            candidate_configs=len(candidates), total_configs=len(df),
            budget=budget if budget_upper is not None else None,
        )
        with st.spinner('正在解释当前推荐…'):
            advice, advice_error = write_recommendation(brief)
        st.session_state.chat.append({
            'role': 'assistant', 'kind': 'result',
            'text': advice or fallback_text(brief), 'models': current_models,
            'candidate_count': len(candidates), 'advice_from_llm': not advice_error,
            'advice_error': advice_error,
            'weights': dict(weights), 'ranking_ids': result.id.tolist(),
        })
        st.rerun()

tab1, tab2, tab3 = st.tabs(['为你推荐', '机型对比', '数据与算法说明'])
with tab1:
    st.subheader('推荐前五款')
    st.caption('先计算具体配置得分，再为每个机型保留最高分配置。匹配度是当前候选集内的相对得分，不是质量评分或购买概率。')
    for index, (_, row) in enumerate(models.iterrows(), 1):
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.subheader(f'{index:02d}  {row["产品名称"]}')
            left.write(f'{row["配置"]}　｜　{row["处理器"]}')
            right.metric('表内价格', f'¥{row.price:,.0f}')
            st.progress(float(row.score), text=f'TOPSIS 相对匹配度 {row.score*100:.1f}%')
            st.write(f'重量 {row.weight:g}g　·　屏幕 {row.screen:g} 英寸　·　{row.refresh:g}Hz　·　存储 {row.storage:g}GB')
            facts = []
            if weights['price'] and budget_upper is not None: facts.append(f'比预算上限低 {budget_upper-row.price:,.0f} 元')
            if weights['screen']: facts.append(f'距目标尺寸 {abs(row.screen-target):.2f} 英寸')
            if weights['weight']: facts.append(f'候选配置重量中位数 {candidates.weight.median():g}g，本配置 {row.weight:g}g')
            st.caption('参数依据：' + '；'.join(facts) if facts else '参数依据见对比页。上述数据描述不代表独立的分数贡献。')
    export = result[['id', '产品名称', '配置', 'brand', 'price', 'weight', 'screen', 'storage', 'refresh', 'score']]
    st.download_button('下载本次配置排序 CSV', export.to_csv(index=False).encode('utf-8-sig'), 'phone_ranking.csv', 'text/csv')
with tab2:
    st.subheader('放在一起比较')
    selected = st.multiselect('选择 2–3 个配置', result.label.tolist(), default=models.label.head(3).tolist(), max_selections=3)
    if selected:
        compare = result.set_index('label').loc[selected]
        fields = ['产品名称', '配置', '价格', '重量', '屏幕大小', '分辨率', '屏幕刷新率', '处理器', '后摄像头', '前摄像头', '充电功率']
        st.dataframe(compare[fields].T, use_container_width=True)
        st.caption('“价格”沿用原表文本。不同处理器、相机像素和功率不能直接等同于实际体验。')
with tab3:
    st.subheader('计算方式与边界')
    st.write('1. 用预算、品牌和最低存储筛选配置。2. 将屏幕尺寸转为与目标尺寸的绝对差。3. 对五列进行向量归一化并乘以用户权重。4. 计算到理想解与负理想解的欧氏距离，得分为负理想距离除以两距离之和。')
    st.write('价格、重量、屏幕距离为成本型；存储与刷新率为效益型。常量列不贡献距离。完全无法区分的候选得分为0.5，同分按价格和记录ID排序。候选集变化可能引起名次变化。')
    st.write('存储换算约定：1TB→1024GB，原始标称保留。括号中的内存类型保留在配置描述中。无额外数据补采，原始工作簿不被修改。')
    st.dataframe(df.iloc[:, :12], hide_index=True, use_container_width=True)
