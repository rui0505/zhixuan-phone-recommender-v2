"""Hash-keyed read-only release loading; safe canonical SKU details."""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit
import streamlit as st
from catalog.repository import CatalogRepository


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_release(db_path: str, manifest_sha: str):
    path=Path(db_path);mp=path.with_name('release_manifest.json')
    if file_sha(mp)!=manifest_sha:raise ValueError('发布清单已变动')
    manifest=json.loads(mp.read_text(encoding='utf-8'))
    actual=file_sha(path)
    if actual!=manifest['snapshot_sha256']:raise ValueError('数据库校验不一致')
    return _load_verified(str(path.resolve()),manifest_sha,actual)


@st.cache_resource(show_spinner=False)
def _load_verified(db_path,manifest_sha,snapshot_sha):
    manifest=json.loads(Path(db_path).with_name('release_manifest.json').read_text(encoding='utf-8'))
    if manifest['status']!='PASS' or manifest['acceptance']['machine_acceptance']!='PASS':
        raise ValueError('发布数据机器验收未通过')
    with closing(sqlite3.connect(Path(db_path).as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('数据库完整性校验失败')
    repo=CatalogRepository(db_path)
    version=repo.dataset_version()
    if version.get('dataset_version')!=manifest['dataset_version'] or version.get('rule_version')!=manifest['feature_rule_version']:
        raise ValueError('数据版本与发布清单不一致')
    return repo,manifest


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


def safe_url(value):
    if not isinstance(value,str):return None
    try:
        p=urlsplit(value)
        return value if p.scheme in ('http','https') and p.hostname and not p.username and not p.password and not any(c in value for c in '\r\n\x00') else None
    except ValueError:return None


def display_records(records):
    if not records:st.info('暂无记录');return
    import pandas as pd
    labels={'score':'相对匹配分','display_name':'产品名称','variant_name':'版本','ram_raw':'运行内存','storage_raw':'存储',
            'price_cny':'记录价格（元）','condition':'成色','observed_at':'记录日期','is_estimated':'估计价',
            'value_num':'数值（空为缺失）','raw_value':'原始记录','unit':'单位','spec_key':'参数','metric_key':'指标',
            'comparison_group':'比较组','subject_type':'作用域','reason':'未进入排名原因','conditions_json':'测试条件'}
    st.dataframe(pd.DataFrame(records),hide_index=True,width='stretch',
                 column_config={key:st.column_config.Column(label) for key,label in labels.items()})


def sku_details(repo,sku_id):
    rows=repo.as_records(repo._q('SELECT s.*,m.display_name,v.variant_name FROM skus s JOIN phone_models m USING(model_id) JOIN phone_variants v USING(variant_id) WHERE sku_id=?',(sku_id,)))
    if not rows:return None
    sku=rows[0];details=repo.get_model_details(sku['model_id'])
    details['sku']=sku
    identities={'sku':sku_id,'variant':sku['variant_id'],'model':sku['model_id']}
    details['specifications']=[r for r in details['specifications'] if identities.get(r['subject_type'])==r['subject_id']]
    details['prices']=[r for r in details['prices'] if r['sku_id']==sku_id]
    return details


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
        url=safe_url(source['url'])
        if url:st.link_button(source['source_id'],url)
        else:st.text(source['source_id']+'：未记载可公开链接')
