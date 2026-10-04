"""正式app.py使用的偏好确认面板；extracted_needs接收队友的需求字典。"""
from copy import deepcopy
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from preferences import (KEYS, LABELS, LEVEL_LABELS, apply_answer,
    build_initial_weights, filter_candidates, next_question, validate_needs)


def receive_extracted_needs(needs):
    """相同需求不会因Streamlit重跑而抹掉用户确认和追问记录。"""
    checked = validate_needs(needs)
    fingerprint = json.dumps(checked, sort_keys=True, ensure_ascii=False)
    if fingerprint == st.session_state.get('blue_model_fingerprint'):
        return
    st.session_state['blue_model_fingerprint'] = fingerprint
    st.session_state['blue_parsed'] = checked
    st.session_state['blue_session'] = None
    st.session_state['blue_revision'] = st.session_state.get('blue_revision', 0) + 1
    st.session_state['blue_source'] = '需求抽取结果'


def render_preference_panel(phones):
    sample = Path(__file__).with_name('example_needs.json').read_text(encoding='utf-8')
    for key, value in {'blue_parsed': validate_needs(json.loads(sample)), 'blue_session': None, 'blue_revision': 0,
                       'blue_round': 0, 'blue_source': '示例需求'}.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if 'extracted_needs' in st.session_state:
        try:
            receive_extracted_needs(st.session_state['extracted_needs'])
        except ValueError as error:
            st.error(f'需求抽取输出不合法：{error}')
            st.stop()
    if st.session_state.blue_source == '示例需求':
        st.info('当前载入示例需求。请修改并确认后推荐；真实语言抽取尚需队友接入。')
    else:
        st.caption('需求来源：' + st.session_state.blue_source)
    with st.expander('开发联调：导入蓝色模块格式的需求JSON'):
        with st.form('import_json'):
            raw = st.text_area('需求JSON', value=sample, height=220, key='blue_input_json')
            imported = st.form_submit_button('读取JSON并检查')
    if imported:
        try:
            parsed = validate_needs(json.loads(raw))
        except (ValueError, json.JSONDecodeError) as error:
            st.error(f'导入失败：{error}。请修正后重新读取。')
            st.stop()
        st.session_state.blue_parsed = parsed
        st.session_state.blue_source = '导入的需求JSON'
        st.session_state.blue_session = None
        st.session_state.blue_revision += 1
        st.success('格式检查通过。请确认或修改下面的需求。')

    parsed = st.session_state.blue_parsed
    if parsed is None:
        st.info('点击“读取JSON并检查”，开始这次推荐。')
        st.stop()

    st.subheader('2. 确认需求和偏好')
    if parsed.get('extraction_status') == 'degraded':
        st.warning('需求抽取有部分字段被降级。请检查下方问题和偏好后再确认。')
    if parsed.get('budget_reference') is not None:
        st.caption(f'原话预算参考值：{parsed["budget_reference"]:g}元；参考值不参与筛选。')
    if parsed['unsupported_needs']:
        st.warning('当前数据无法评价这些需求：' + '；'.join(parsed['unsupported_needs']))
    if parsed['clarification_questions']:
        st.write('需求抽取模块希望澄清：' + '；'.join(parsed['clarification_questions']))
        st.caption('请与用户确认后修改字段；原JSON中的澄清问题作为记录保留。')
    with st.expander('查看用户原话依据'):
        st.json(parsed['evidence'])
        st.caption('这里保留抽取时的原话。手动改动会在权重来源中标记。')

    version = st.session_state.blue_revision
    hard = parsed['hard_constraints']
    with st.form(f'confirm_{version}'):
        c1, c2 = st.columns(2)
        with c1:
            use_min_budget = st.checkbox('设置预算下限', value=hard.get('min_price') is not None)
            min_budget = st.number_input('最低预算（元，仅启用时生效）', min_value=0.0,
                value=float(hard.get('min_price') or 0), step=100.0)
            use_budget = st.checkbox('设置预算上限', value=hard['max_price'] is not None)
            budget = st.number_input('最高预算（元，仅启用时生效）', min_value=0.0,
                value=float(3000 if hard['max_price'] is None else hard['max_price']), step=100.0)
            use_storage = st.checkbox('设置最低存储', value=hard['min_storage_gb'] is not None)
            storage = st.number_input('最低存储（GB，仅启用时生效）', min_value=1.0,
                                      value=float(hard['min_storage_gb'] or 256), step=64.0)
            brands = st.multiselect('品牌，留空表示全部',
                options=sorted(set(phones.brand) | set(hard['brands'])), default=hard['brands'])
            excluded = st.multiselect('排除品牌',
                options=sorted(set(phones.brand) | set(hard.get('excluded_brands', []))),
                default=hard.get('excluded_brands', []))
        with c2:
            use_screen = st.checkbox('指定目标屏幕尺寸', value=parsed['target_screen_inches'] is not None)
            target = st.number_input('目标屏幕（英寸，仅启用时生效）', min_value=3.0,
                max_value=10.0, value=float(parsed['target_screen_inches'] or 6.5), step=0.1)
            st.caption('未启用目标尺寸时，屏幕暂不计分，其他项重新归一化。')
            levels = {}
            options = list(LEVEL_LABELS)
            for key in KEYS:
                levels[key] = st.selectbox(LABELS[key] + '的重要程度', options,
                    index=options.index(parsed['preferences'][key]),
                    format_func=lambda value: LEVEL_LABELS[value], key=f'level_{version}_{key}')
        agreed = st.checkbox('我已检查硬条件、默认偏好、屏幕目标和待澄清内容')
        confirmed = st.form_submit_button('确认需求并生成推荐')

    if confirmed:
        if not agreed:
            st.error('请先检查需求，并勾选确认。')
        else:
            needs = deepcopy(parsed)
            needs['hard_constraints'] = {
                'min_price': min_budget if use_min_budget else None,
                'max_price': budget if use_budget else None,
                'min_storage_gb': storage if use_storage else None, 'brands': brands,
                'excluded_brands': excluded,
            }
            needs['target_screen_inches'] = target if use_screen else None
            needs['preferences'] = levels
            # 重确认失败时也必须清掉旧的已确认需求，避免继续展示旧排序。
            st.session_state.blue_session = None
            try:
                initial = build_initial_weights(needs)
                for key in KEYS:
                    if levels[key] != parsed['preferences'][key]:
                        initial['sources'][key] = 'manual_level'
                st.session_state.blue_session = {
                    'needs': needs, 'initial': initial,
                    'current_weights': dict(initial['weights']),
                    'answered_ids': [], 'history': [],
                }
                st.session_state.blue_round += 1
                st.success('本次需求已确认，旧追问记录已清空。')
            except ValueError as error:
                st.error(str(error))
                st.stop()
        if not agreed:
            st.stop()

    session = st.session_state.blue_session
    if session is None:
        st.info('确认需求后，会展示权重和推荐。')
        st.stop()
    st.caption('以下结果使用最近一次点击“确认需求并生成推荐”时保存的需求；修改表单后需重新确认。')
    candidates = filter_candidates(phones, session['needs'])
    st.subheader('3. 通过取舍进一步确认')
    if candidates.empty:
        st.warning('没有符合硬条件的配置。请修改上方条件，再重新确认。')
    else:
        q = next_question(session['current_weights'], session['answered_ids'],
                          session['needs']['preferences'])
        if q:
            st.caption('以下为假设情景，不表示真实手机只有这两项差异。最多回答两道。')
            with st.form(f'question_{version}_{st.session_state.blue_round}_{q["id"]}'):
                st.write(q['text'])
                options = {'a': q['a_label'], 'b': q['b_label'],
                           'indifferent': '没有明显倾向', 'skip': '跳过'}
                answer = st.radio('你的选择', list(options), index=3,
                                  format_func=lambda value: options[value])
                answered = st.form_submit_button('提交回答并更新推荐')
            if answered:
                try:
                    change = apply_answer(session['current_weights'], q, answer,
                                          session['answered_ids'])
                    change['record']['answer_label'] = options[answer]
                    session['current_weights'] = change['weights']
                    session['answered_ids'] = change['answered_ids']
                    session['history'].append(change['record'])
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        else:
            st.info('本轮追问已结束，或没有适合当前启用指标的题目。')

    st.subheader('4. 当前权重')
    source_labels = {'system_default': '系统默认', 'confirmed_level': '确认后的等级',
                     'manual_level': '手动修改等级'}
    table = []
    for key in KEYS:
        initial = session['initial']
        source = source_labels[initial['sources'][key]]
        if key in initial['inactive_keys']:
            source += '；无目标尺寸，暂不计分'
        table.append({
            '指标': LABELS[key], '偏好等级': LEVEL_LABELS[session['needs']['preferences'][key]],
            '原始分值': initial['raw_scores'][key], '有效分值': initial['effective_scores'][key],
            '初始权重（%）': round(initial['weights'][key] * 100, 2),
            '当前权重（%）': round(session['current_weights'][key] * 100, 2), '来源': source,
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    st.caption('等级映射与每次25%的相对调整是开发规则；不代表测出了真实心理权重。计算使用完整精度。')
    if session['history']:
        with st.expander('查看追问修正记录'):
            st.json(session['history'])


    st.download_button('下载本轮偏好与追问记录',
        json.dumps(session, ensure_ascii=False, indent=2).encode('utf-8'),
        'preference_session.json', 'application/json')
    return session
