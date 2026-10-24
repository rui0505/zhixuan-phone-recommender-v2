"""Run: python -m streamlit run algorithm_lab.py --server.port 8502

An experimental entrypoint using the same released catalog as the production app.
No paid provider calls or changes to the deployed ranking endpoint.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from algorithms import VERSION
from algorithms.ranking import DecisionProblem, midranks
from algorithms.preference import PreferenceSession, SessionConfig, digest
from catalog.adapter import build_input
from catalog.release import ReleaseStore

ROOT = Path(__file__).resolve().parent
LABELS = {
    'price_cny': '价格（元，越低越好）', 'weight_g': '重量（克，越轻越好）',
    'storage_gb': '存储（GB）', 'refresh_rate_hz': '刷新率（Hz）',
    'screen_size_inch': '屏幕（英寸，接近目标）',
    'dxomark_photo': '同协议照片评测分', 'honor_of_kings_avg_fps': '同条件游戏实测帧率',
}
STRATEGIES = {'disagreement': '信息增益＋算法分歧', 'information': '仅信息增益',
              'random': '随机追问（对照）'}
OUTPUTS = {'topsis': '学习后 TOPSIS', 'fusion': 'TOPSIS＋TODIM 融合（实验对照）',
           'vikor': '学习后 VIKOR Q 指数', 'todim': '经典 TODIM（实验对照）',
           'utility': '加性效用（学习模型对照）'}


def main():
    st.set_page_config(page_title='知选 · 算法实验室', page_icon='🔬', layout='wide')
    st.title('知选 · 算法实验室')
    st.caption('确认条件 → 比较两款手机 → 学习偏好 → 对照推荐结果')
    st.info('竞赛研究版本：支持率表示模型下的排名稳定性，不是购买或满意概率。'
            '这里使用目录快照，不保证实时价格；未调用付费大模型。')
    try:
        release = ReleaseStore(Path(os.environ.get('PHONE_CATALOG_PATH',
                                                   ROOT/'data/catalog.sqlite'))).get()
    except (ValueError, OSError) as exc:
        st.error(f'目录发布校验失败：{exc}')
        return
    repo = release.repo
    models = repo.as_records(repo.list_models())
    brands = sorted({row['brand'] for row in models if row.get('brand')})
    with st.sidebar:
        st.header('先确定可接受的范围')
        mode = st.selectbox('成色', ['new', 'used', 'all'], key='lab_mode',
                            format_func=lambda x: {'new': '新机', 'used': '二手', 'all': '全部'}[x])
        lo = st.number_input('最低预算（元）', 0, 100000, 1000, 100, key='lab_min_price')
        hi = st.number_input('最高预算（元）', 0, 100000, 6000, 100, key='lab_max_price')
        storage = st.selectbox('最低存储（GB）', [0, 128, 256, 512, 1024], index=2,
                               key='lab_storage')
        includes = st.multiselect('限定品牌（留空表示不限）', brands, key='lab_brands')
        st.subheader('初始偏好')
        weights = {}
        defaults = [5, 2, 2, 1, 0, 0, 0]
        for (key, label), default in zip(LABELS.items(), defaults):
            weights[key] = st.slider(label, 0, 10, default, key='lab_w_'+key)
        target = st.number_input('目标屏幕（英寸，仅启用屏幕指标时使用）', 3., 10., 6.5, .1,
                                 key='lab_target')
        with st.expander('实验参数与对照'):
            strategy = st.selectbox('选题策略', list(STRATEGIES), index=1, format_func=STRATEGIES.get,
                                    key='lab_strategy')
            theta = st.number_input('TODIM 损失衰减 θ（非心理测量）', .05, 20., 1., .25)
            alpha = st.slider('TOPSIS 融合比例', 0., 1., .5, .05)
            k = st.number_input('RRF 平滑参数', 1., 100., 10., 1.)
            strength = st.number_input('先验强度', 0., 100., 12., 1.)
            max_questions = st.slider('最多追问数', 1, 10, 5)
            st.caption('初始滑块是先验中心的参考；粒子先验经过平滑，未作答时均值可能不同。'
                       '零权重表示关闭，学习不会重新开启。')
    needs = {'budget': {'min': lo, 'max': hi}, 'min_storage': storage or None,
             'brands': {'include': includes, 'exclude': []}}
    config = SessionConfig(theta=theta, alpha=alpha, k=k, prior_strength=strength,
                           max_questions=max_questions, strategy=strategy)
    actual_target = target if weights['screen_size_inch'] > 0 else None
    signature = digest({'algorithm_version': VERSION, 'release': release.key,
                        'needs': needs, 'weights': weights,
                        'target': actual_target, 'mode': mode, 'config': asdict(config)})
    # Check before rendering confirmation widget so Streamlit can safely reset its state.
    if st.session_state.get('lab_signature') not in (None, signature):
        st.session_state.pop('lab_session', None)
        st.session_state['lab_confirm'] = False
        st.warning('条件、算法参数或数据版本已变化，请重新确认并开始；旧回答未沿用。')
    st.session_state['lab_signature'] = signature
    st.checkbox('我已确认预算、偏好和数据限制，允许在这些条件内比较', key='lab_confirm')
    if st.button('确认条件并开始实验', key='lab_start', type='primary'):
        st.session_state.pop('lab_session', None)
        if not st.session_state.lab_confirm:
            st.warning('请先确认条件。')
        else:
            try:
                di = build_input(repo, needs, weights, actual_target, mode)
                session = PreferenceSession(DecisionProblem(di, actual_target), config)
                st.session_state['lab_session'] = session
                st.session_state['lab_backup_count'] = len(di['backup_indices'])
            except ValueError as exc:
                st.error(str(exc))
    session = st.session_state.get('lab_session')
    if session is None:
        st.write('左侧确定范围和初始偏好，然后开始实验。无需训练数据或 API 密钥。')
        return
    problem = session.problem
    a, b, c = st.columns(3)
    a.metric('可比配置', len(problem.ids))
    b.metric('已回答 / 上限', f'{len(session.history)} / {config.max_questions}')
    c.metric('缺指标的备选', st.session_state.get('lab_backup_count', 0))
    if st.button('撤销上一个回答', disabled=not session.history, key='lab_undo'):
        session.undo()
        st.rerun()
    question = session.question()
    if question:
        if st.session_state.get('lab_question_id') != question['id']:
            st.session_state['lab_answer'] = None
            st.session_state['lab_question_id'] = question['id']
        st.subheader('在当前条件下，你更愿意选哪款？')
        st.caption('比较所有列出的指标；没有明显倾向可以跳过。'
                   '这里只展示有来源的参数，不从参数推断拍照或游戏体验。')
        columns = st.columns(2)
        for key, column in zip(('a', 'b'), columns):
            sku = question[key]
            i = problem.ids.index(sku)
            candidate = problem.candidates.iloc[i]
            with column:
                st.markdown(f"**{key.upper()} · {candidate.get('display_name', sku)}**")
                st.caption(f"{candidate.get('variant_name', '')} · {sku}")
                st.dataframe(pd.DataFrame({
                    '比较项目': [LABELS.get(k, k) for k in problem.keys],
                    '原始数值': problem.raw[i],
                }), hide_index=True, width='stretch')
        with st.form('lab_choice_form'):
            answer = st.radio('你的选择', ['a', 'b', 'indifferent', 'skip'],
                               format_func=lambda v: {'a': '更喜欢 A', 'b': '更喜欢 B',
                                                      'indifferent': '没有明显倾向',
                                                      'skip': '跳过此题'}[v],
                               index=None, horizontal=True, key='lab_answer')
            if st.form_submit_button('提交选择并更新', key='lab_submit'):
                if answer is None:
                    st.warning('请选择一个回答，或选择跳过。')
                else:
                    session.answer(question['id'], answer)
                    st.rerun()
        with st.expander('为什么问这一题？'):
            st.write(f"策略：{STRATEGIES[config.strategy]}；"
                     f"预计信息增益 {question['information_gain']:.4f} nats；"
                     f"两路严格反序支持率 {question['disagreement']:.1%}。")
            st.caption('这是选题模型的估计，不是这次回答一定能带来的效果。')
    else:
        st.success('本轮追问结束：达到上限，或当前没有符合条件的有效取舍问题。')
    report = session.report()
    st.subheader('偏好如何变化')
    weight_frame = pd.DataFrame(report['weight_summary'])
    weight_frame['metric'] = weight_frame['metric'].map(LABELS)
    st.dataframe(weight_frame.rename(columns={'metric': '指标', 'initial': '初始参考',
                 'mean': '当前后验均值', 'p05': '后验5%分位', 'p95': '后验95%分位'}),
                 hide_index=True, width='stretch')
    st.caption('区间表示当前模型仍认为可能的权重范围；回答少时保留不确定性。')
    if report['posterior_is_concentrated']:
        st.warning('当前权重主要由少量粒子支撑，结果对先验更敏感；可撤销可疑回答并比较对照算法。')
    st.subheader('推荐与算法对照')
    output = st.selectbox('查看哪种排序', list(OUTPUTS), format_func=OUTPUTS.get, key='lab_output')
    if output in ('fusion', 'todim'):
        st.warning('经典 TODIM 的损失项含逆权重，可能与偏好权重方向冲突。融合未被验证普遍更好，'
                   '请与学习后 TOPSIS、VIKOR 对照。')
    results = pd.DataFrame(report['ranking']).set_index('sku_id')
    results['top1_support'] = [row['stability'][output]['top1_support'] for row in report['ranking']]
    results['rank_std'] = [row['stability'][output]['rank_std'] for row in report['ranking']]
    names = problem.candidates.set_index('sku_id')
    for key in ('display_name', 'storage_gb', 'price_cny'):
        if key in names:
            results[key] = names[key]
    for key in ('baseline_topsis', 'topsis', 'todim', 'fusion', 'vikor', 'utility'):
        results[key+'_rank'] = midranks(results[key].to_numpy())
    results = results.sort_values([output+'_rank'], kind='stable')
    visible = [k for k in ('display_name', 'storage_gb', 'price_cny') if k in results]
    visible += ['baseline_topsis_rank', 'topsis_rank', 'todim_rank', 'fusion_rank',
                'vikor_rank', 'utility_rank', 'top1_support', 'rank_std']
    st.dataframe(results[visible].rename(columns={
        'display_name': '手机', 'storage_gb': '存储GB', 'price_cny': '快照价格',
        'baseline_topsis_rank': '原TOPSIS名次', 'topsis_rank': '后验TOPSIS名次',
        'todim_rank': '后验TODIM名次', 'fusion_rank': '融合名次',
        'vikor_rank': '后验VIKOR-Q名次', 'top1_support': '第一名支持率',
        'rank_std': '粒子名次标准差', 'utility_rank': '加性效用名次',
    }), width='stretch')
    st.caption(f'表格按{OUTPUTS[output]}排列；稳定性也对应所选算法。'
               '并列名次采用平均名次，ID 顺序不代表优劣。'
               '后验各路名次由各路平均分生成，融合则对每个粒子的名次融合后再取期望。')
    with st.expander('证据、回答记录与实验边界'):
        st.json({'算法版本': VERSION, '目录发布': release.key,
                 '决策指纹': problem.decision_sha256,
                 '有效粒子数': report['effective_particles'],
                 '回答记录': report['history'], '限制': report['limitations'],
                 '来源': problem.provenance})
    exported = {**report, 'release': release.key, 'display_method': output, 'confirmed_request':
                {'needs': needs, 'weights': weights, 'target_screen': actual_target, 'mode': mode}}
    st.download_button('下载本轮完整实验记录 JSON',
                       json.dumps(exported, ensure_ascii=False, indent=2, allow_nan=False),
                       'zhixuan-algorithm-session.json', 'application/json')


if __name__ == '__main__':
    main()
