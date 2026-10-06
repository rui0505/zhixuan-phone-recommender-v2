"""Production: read-only release → confirmed needs → TOPSIS → bound evidence."""
import os
import sqlite3
from pathlib import Path
import streamlit as st
from catalog.adapter import build_input,resolve_brands
from catalog.evidence import build_evidence,digest,render_template
from engine import rank_decision_input
from ui.catalog_views import (file_sha,load_release,invalidate_confirmation,clear_result,
    display_records,render_details,sku_details,safe_url)
from ui.export_results import export_results
from providers.env import load_dotenv,llm_enabled

st.set_page_config(page_title='知选 · 可解释手机推荐',page_icon='📱',layout='wide')
st.title('知选 · 可解释手机推荐')
st.caption('先确认需求，再比较可比数据。TOPSIS 排序与解释引用同一数据版本。')
state=st.session_state
db=Path(os.environ.get('PHONE_CATALOG_PATH',str(Path(__file__).resolve().parent/'data/catalog.sqlite')))
try:
    manifest_sha=file_sha(db.with_name('release_manifest.json'))
    repo,manifest=load_release(str(db),manifest_sha)
except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error):
    clear_result(state);state['manual_confirm']=False
    st.error('发布数据校验失败或文件不可用；已停止推荐。请维护者检查发布快照与清单。')
    st.stop()
if invalidate_confirmation(state,manifest_sha+':'+manifest['snapshot_sha256']):
    st.warning('数据版本或快照已更新，旧排名与解释已失效，请重新确认需求。')
all_rows=repo.as_records(repo.load_catalog(mode='all'))
state['catalog_rows']=all_rows
st.caption(f"数据 {manifest['dataset_version']} / 规则 {manifest['feature_rule_version']} / 算法 topsis-1.0.0 / 快照 {manifest['snapshot_sha256'][:12]}")
counts=st.columns(3)
for col,key,title in zip(counts,('model_id','variant_id','sku_id'),('机型','版本','配置 SKU')):
    col.metric(title,len({r[key] for r in all_rows}))
acceptance=manifest['acceptance']
st.warning(f"机器验收 {acceptance['machine_acceptance']}；总体验收 {acceptance['overall_acceptance']}；WPS {acceptance['wps_review']}。人工抽查仍待完成。")
st.warning('游戏数据缺关键测试条件时只展示备选，不用刷新率或处理器型号冒充实测。1% Low 未形成有效覆盖，不推算完整游戏性能总分。')
st.caption('VIKOR 未实现。拍照暂用同协议 DXOMARK 照片子分，不代表所有场景。权重为试验设置；价格是历史记录；成色未知的记录不混入新机或二手。')

load_dotenv()
for key in ('PHONE_ENABLE_LLM','PHONE_LLM_BUDGET_APPROVED','PHONE_LLM_GATEWAY_LIMITS_APPROVED','LLM_BASE_URL','LLM_MODEL','LLM_API_KEY'):
    if not os.environ.get(key):
        try:
            value=st.secrets[key]
            if value is not None:os.environ[key]=str(value)
        except (KeyError,FileNotFoundError,RuntimeError):pass
enabled=llm_enabled()
if enabled:
    from providers.llm import llm_status
    enabled,_=llm_status()
st.info('对话框需要接入经预算及网关限额审核的大模型；本次默认关闭。手动确认始终可用。')
prompt=st.chat_input('可选：补充购机需求，抽取后仍需手动确认',disabled=not enabled)
if prompt:
    from needs_extraction import extract_needs
    from pipeline import join_history,MAX_ASK_ROUNDS
    from bridge import bridge_contract
    state['need_history']=[*state.get('need_history',[]),prompt]
    extracted=extract_needs(join_history(state['need_history']));state['extracted_needs']=extracted
    clear_result(state);state['manual_confirm']=False
    if extracted['status']!='failed':
        known=sorted({r['brand'] for r in all_rows})
        try:
            extracted['brands']={side:resolve_brands(extracted['brands'][side],known) for side in ('include','exclude')}
        except ValueError:
            extracted['status']='failed';extracted['meta']['error']='抽取的品牌无法唯一匹配；不自动放宽品牌条件，请手动选择后确认。'
    if extracted['status']!='failed':
        bridged,_=bridge_contract(extracted,known_brands=sorted({r['brand'] for r in all_rows}))
        state['manual_brands']=list(bridged.brands);state['manual_excludes']=list(bridged.excluded_brands)
        for edge in ('min','max'):
            value=extracted['budget'][edge];state['manual_use_'+edge]=value is not None
            if value is not None:state['manual_'+edge]=float(value)
        state['manual_storage']=float(extracted.get('min_storage') or 0)
        if extracted.get('target_screen') is not None:state['manual_target']=float(extracted['target_screen'])
        for key,value in bridged.weights.items():state['manual_w_'+key]=min(10,max(0,int(round(value*10))))
        state['ask_rounds']=min(MAX_ASK_ROUNDS,state.get('ask_rounds',0)+1)
if state.get('extracted_needs'):
    with st.expander('需求抽取与待澄清内容',expanded=True):
        extracted=state['extracted_needs']
        if extracted['status']=='failed':st.error(extracted['meta']['error'])
        else:
            st.json(extracted)
            for item in extracted['clarifications']:
                st.text(item['question'] if state.get('ask_rounds',0)<2 else '请在手动条件中确认：'+item['field'])
            st.caption('含糊预算只保留参考值，不自动变成上限。拍照/游戏权重使用手动控件。')
if st.button('清空需求与结果'):
    clear_result(state)
    for key in ('need_history','extracted_needs','ask_rounds'):state.pop(key,None)
    for key in list(state):
        if key.startswith('manual_'):state.pop(key,None)
    st.rerun()

labels={'price':'价格更低','weight':'机身更轻','storage':'存储更大','refresh':'屏幕刷新率更高（非游戏性能）',
    'screen':'屏幕接近目标','photo':'拍照：DXOMARK 照片子分','game':'游戏：王者荣耀实测平均帧率'}
defaults={'price':5,'weight':2,'storage':2,'refresh':1,'screen':0,'photo':0,'game':0}
for key,value in {'manual_min':0.0,'manual_max':5000.0,'manual_storage':0.0,'manual_target':6.5,
                  **{'manual_w_'+key:value for key,value in defaults.items()}}.items():state.setdefault(key,value)
with st.sidebar:
    st.header('确认你的需求')
    mode_label=st.selectbox('价格成色范围',['新机','二手','全部（含成色未知）'],key='manual_mode')
    mode={'新机':'new','二手':'used','全部（含成色未知）':'all'}[mode_label]
    use_min=st.checkbox('启用预算下限',key='manual_use_min')
    min_price=st.number_input('预算下限（元）',min_value=0.0,value=None,step=100.0,key='manual_min')
    use_max=st.checkbox('启用预算上限',key='manual_use_max')
    max_price=st.number_input('预算上限（元）',min_value=0.0,value=None,step=100.0,key='manual_max')
    min_storage=st.number_input('最低存储（GB，0 表示不筛选）',min_value=0.0,value=None,step=128.0,key='manual_storage')
    brands=sorted({r['brand'] for r in all_rows})
    included=st.multiselect('仅看这些品牌（空表示不限）',brands,key='manual_brands')
    excluded=st.multiselect('排除这些品牌',brands,key='manual_excludes')
    target=st.slider('目标屏幕尺寸（英寸）',3.0,10.0,None,0.1,key='manual_target')
    st.caption('以下为试验默认权重；0 表示不参与排序。未提及不等于已理解你的偏好。')
    weights={key:st.slider(label,0,10,None,key='manual_w_'+key) for key,label in labels.items()}
    checked=st.checkbox('我已检查条件、权重和数据限制',key='manual_confirm')
    confirm=st.button('确认需求并生成推荐',type='primary')
needs={'budget':{'min':min_price if use_min else None,'max':max_price if use_max else None},
       'min_storage':min_storage or None,'brands':{'include':included,'exclude':excluded}}
request={'needs':needs,'weights':weights,'target':target,'mode':mode};request_hash=digest(request)
if state.get('request_hash') and state['request_hash']!=request_hash:
    clear_result(state);st.info('条件已修改，旧结果已清除；请重新确认。')
if confirm:
    clear_result(state)
    if not checked:st.error('请先检查并勾选确认。')
    elif set(included)&set(excluded):st.error('同一品牌不能同时包含和排除。')
    else:
        try:
            di=build_input(repo,needs,weights,target,mode);result=rank_decision_input(di,target_screen=target)
            state['catalog_input']=di;state['catalog_result']=result
            state['confirmed_request']=request;state['request_hash']=request_hash
            if result.status=='ready':state['catalog_bundle']=build_evidence(di,result,needs)
        except (ValueError,KeyError,TypeError,OverflowError):
            clear_result(state)
            st.error('条件、权重或可比输入无效；请检查预算上下界和正权重。不生成推荐成绩。')
recommend,catalog,details,compare=st.tabs(['推荐结果','全部目录','配置详情与来源','按 SKU 对比'])
with recommend:
    result=state.get('catalog_result');di=state.get('catalog_input')
    if result is None:st.info('请选择条件与权重，检查后点击“确认需求并生成推荐”。')
    else:
        st.caption(f"数据 {result.dataset_version} / 规则 {result.feature_rule_version} / 算法 {result.algorithm_version}")
        st.text(result.reason);st.json(di['coverage_summary'],expanded=False)
        if result.status!='ready':st.warning(result.reason)
        else:
            st.subheader('本次可比候选的 TOPSIS 相对排序')
            display_records([{k:r.get(k) for k in ('sku_id','score','display_name','variant_name','ram_raw','storage_raw','price_cny','condition','observed_at','is_estimated','price_id')} for r in repo.as_records(result.ranked)])
            bundle=state['catalog_bundle']
            with st.expander('已核验程序解释与事实依据'):
                st.text(render_template(bundle));st.json(bundle,expanded=False)
                links=set()
                for fact in bundle['facts'].values():
                    for source in fact['sources']:
                        url=safe_url(source.get('url'))
                        if url and source['source_id'] not in links:
                            links.add(source['source_id']);st.link_button(source['source_id'],url)
            st.caption('CSV/XLSX 是展示副本：公式型文本前加单引号，数据库原文不变。缺失留空；JSON 用 null。')
            for fmt,mime in (('csv','text/csv'),('xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),('json','application/json')):
                st.download_button('下载 '+fmt.upper(),export_results(result,bundle,fmt),file_name='zhixuan_'+result.dataset_version+'.'+fmt,mime=mime)
        if not result.backups.empty:
            st.subheader('数据不足或不可比的备选（不评分）');display_records(repo.as_records(result.backups))
            with st.expander('备选未进入排名的原因'):
                display_records([{'sku_id':p['sku_id'],'metric_key':ev['metric_key'],'reason':ev.get('missing_reason'),'comparison_group':ev.get('comparison_group')} for i,p in enumerate(di['provenance']) if i in di['backup_indices'] for ev in p['criteria'] if ev['value'] is None and di['weights'][ev['metric_key']]>0])
columns=('sku_id','model_id','variant_id','display_name','variant_name','ram_raw','storage_raw','memory_type','price_cny','condition','observed_at','is_estimated','price_id')
with catalog:
    st.subheader('当前发布目录')
    st.caption('此页包含全部成色，只用于查阅。推荐区采用你确认的成色与硬条件。图片未授权发布，暂不展示。')
    display_records([{k:r.get(k) for k in columns} for r in all_rows])
by_id={r['sku_id']:r for r in all_rows}
def label(ident):
    r=by_id[ident];return f"{r['display_name']} / {r['variant_name']} / {r['ram_raw']} / {r['storage_raw']} / {ident}"
with details:
    if by_id:
        sku=st.selectbox('选择配置 SKU',list(by_id),format_func=label,key='detail_sku');render_details(repo,sku)
with compare:
    chosen=st.multiselect('选择最多 4 个规范 SKU',list(by_id),format_func=label,max_selections=4,key='compare_skus')
    if chosen:
        display_records([{k:by_id[ident].get(k) for k in columns} for ident in chosen])
        for ident in chosen:
            with st.expander(label(ident)):
                detail=sku_details(repo,ident)
                display_records([{k:r.get(k) for k in ('spec_key','value_num','unit','raw_value','subject_type','comparison_group','spec_id')} for r in detail['specifications']])
    else:st.info('按 ID 选择配置后对比；同名不同版本不会合并。')
