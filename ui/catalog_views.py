"""Hash-keyed read-only release loading; safe canonical SKU details."""
from pathlib import Path
import streamlit as st
from catalog.release import file_sha, verify_release
from catalog.read_views import safe_url, safe_urls, sku_details

# Streamlit 每次交互都会重跑脚本。全文件哈希在这里同样不该每轮都算：
# 先用 (mtime_ns, size) 判断是否变更，只有变了才做全库 SHA256。
# 校验语义不变——文件一改，缓存立刻失效并重新走完整校验。
_STAT_CACHE = {}


def load_release(db_path: str, manifest_sha: str):
    import json
    path=Path(db_path);mp=path.with_name('release_manifest.json')
    db_stat=path.stat();mp_stat=mp.stat()
    stamp=(db_stat.st_mtime_ns,db_stat.st_size,mp_stat.st_mtime_ns,mp_stat.st_size)
    cached=_STAT_CACHE.get(str(path))
    if cached is not None and cached[0]==(stamp,manifest_sha):
        return cached[1]
    if file_sha(mp)!=manifest_sha:raise ValueError('发布清单已变动')
    manifest=json.loads(mp.read_text(encoding='utf-8'))
    actual=file_sha(path)
    if actual!=manifest['snapshot_sha256']:raise ValueError('数据库校验不一致')
    release=_load_verified(str(path.resolve()),manifest_sha,actual)
    _STAT_CACHE[str(path)]=((stamp,manifest_sha),release)
    return release


@st.cache_resource(show_spinner=False)
def _load_verified(db_path,manifest_sha,snapshot_sha):
    release=verify_release(Path(db_path),manifest_sha,snapshot_sha)
    return release.repo,release.manifest


def clear_result(state):
    for key in ('catalog_result','catalog_bundle','catalog_input','confirmed_request','request_hash'):
        state.pop(key,None)


def invalidate_confirmation(state,release_key):
    old=state.get('release_key');changed=old is not None and old!=release_key
    if changed:
        clear_result(state);state['manual_confirm']=False
        state.pop('detail_sku',None);state.pop('compare_skus',None)
    state['release_key']=release_key
    return changed


def display_records(records):
    if not records:st.info('暂无记录');return
    import pandas as pd
    labels={'score':'相对匹配分','display_name':'产品名称','variant_name':'版本','ram_raw':'运行内存','storage_raw':'存储',
            'price_cny':'记录价格（元）','condition':'成色','observed_at':'记录日期','is_estimated':'估计价',
            'value_num':'数值（空为缺失）','raw_value':'原始记录','unit':'单位','spec_key':'参数','metric_key':'指标',
            'comparison_group':'比较组','subject_type':'作用域','reason':'未进入排名原因','conditions_json':'测试条件'}
    st.dataframe(pd.DataFrame(records),hide_index=True,width='stretch',
                 column_config={key:st.column_config.Column(label) for key,label in labels.items()})


def render_details(repo,sku_id: str):
    detail=sku_details(repo,sku_id)
    if not detail:st.warning('该 SKU 不在当前发布版本中');return
    row=detail['sku']
    st.text(f"{row['display_name']} / {row['variant_name']} / {row['ram_raw']} / {row['storage_raw']} / {sku_id}")
    st.caption('图片未发布：使用依据待确认；本地原图保持不动。以下为原始记录层，不等于本次排序全部可用。')
    st.markdown('#### 该配置及通用规格')
    display_records([{k:r.get(k) for k in ('spec_key','value_num','unit','raw_value','subject_type','comparison_group','parse_status','spec_id','source_id')} for r in detail['specifications']])
    st.markdown('#### 全部价格快照（非本次所选价格承诺）')
    display_records([{k:r.get(k) for k in ('price_id','amount','condition','price_kind','observed_at','is_estimated','source_sale_status','source_id')} for r in detail['prices']])
    st.markdown('#### 测试运行与条件')
    display_records([{k:r.get(k) for k in ('run_id','benchmark','institution','protocol','run_date','applicable_scope','scope_note','conditions_json')} for r in detail['benchmark_runs']])
    display_records([{k:r.get(k) for k in ('metric_key','value_num','unit','comparison_group','missing_reason','run_id','observation_id')} for r in detail['observations']])
    st.caption('机型级评测是否适用此版本/配置，由决策输入的作用域及硬件冲突检查决定；详情可见不表示已进入排名。')
    ids={r.get('source_id') for group in ('specifications','prices','benchmark_runs') for r in detail[group] if r.get('source_id')}
    for spec in detail['specifications']:
        ids.update(p.get('source_id') for p in repo.get_spec_provenance(spec['spec_id']) if p.get('source_id'))
    st.markdown('#### 来源链接')
    for source in repo.as_records(repo._q('SELECT source_id,url,publisher FROM sources ORDER BY source_id')):
        if source['source_id'] not in ids:continue
        urls=safe_urls(source['url'])
        if urls:
            for number,url in enumerate(urls,1):st.link_button(source['source_id']+f' / {number}',url)
        else:st.text(source['source_id']+'：未记载可公开链接')
