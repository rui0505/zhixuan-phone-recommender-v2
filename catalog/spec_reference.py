"""Versioned specification proxies. Scores are estimates, never measurements.

Fixed anchors keep a phone's dimension scores invariant under candidate filters.
Null means missing evidence; zero is a valid computed score.
"""
from copy import deepcopy
import math
import re
import hashlib
import json
from pathlib import Path

from .adapter import build_input, canonical_weights
from .repository import CatalogRepository
from .chipset_tiers import normalise_chipset

RULE_VERSION='spec-reference-1.0.0'
METRICS={
    'storage_gb':('ref_storage','存储容量参考','20 × log2(GB / 64)'),
    'weight_g':('ref_weight','轻便参考','100 × (260 - 克) / 120'),
    'battery_capacity_mah':('ref_battery','电池容量参考','100 × (mAh - 3000) / 5000；容量不等于续航'),
    'chipset_tier':('ref_chipset','处理器参考','版本化第三方处理器档位，含换算与估算，非本站实测'),
    'screen_size_inch':('ref_screen','屏幕尺寸匹配','100 × (1 - abs(英寸 - 目标英寸) / 1.5)；不代表画质'),
    'dxomark_photo':('ref_photo','影像规格参考','40 × clip(log2(主摄MP / 8) / log2(200 / 8)) + 超广角30 + 长焦30；不代表成片质量'),
    'honor_of_kings_avg_fps':('ref_game','游戏参考（规格估算）','80%处理器参考 + 20%电池容量参考；非帧率或实测续航'),
}
RAW=('storage_gb','weight_g','battery_capacity_mah','chipset_tier','screen_size_inch')


def reference_table(repo):
    path=Path(repo.db_path).with_name('chipset-primary-reference.json')
    try:
        content=path.read_bytes(); doc=json.loads(content)
        if not doc.get('scores') or any(not _num(v) or v>100 for v in doc['scores'].values()):raise ValueError()
        if set(doc['scores'])!=set(doc['entries']):raise ValueError()
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise ValueError('规格参考芯片表缺失或无效') from exc
    return doc,hashlib.sha256(content).hexdigest()


def reference_version(repo):
    return RULE_VERSION+'-'+reference_table(repo)[1][:12]


def _num(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def _clip(value):
    return round(max(0,min(100,value)),2)


def camera_megapixels(text):
    if not isinstance(text,str):return None
    # The documented first/main camera only; do not select the largest arbitrary lens.
    main=re.split(r'[，,；;]',text)[0]
    match=re.search(r'(\d+(?:\.\d+)?)\s*(MP|万)',main,re.I)
    if not match:return None
    value=float(match[1])/(100 if match[2]=='万' else 1)
    return value if value>0 else None


def reference_values(raw,target,camera):
    v={key:None for key,_,_ in METRICS.values()}
    if _num(raw.get('storage_gb')):v['ref_storage']=_clip(20*math.log2(raw['storage_gb']/64))
    if _num(raw.get('weight_g')):v['ref_weight']=_clip(100*(260-raw['weight_g'])/120)
    if _num(raw.get('battery_capacity_mah')):v['ref_battery']=_clip(100*(raw['battery_capacity_mah']-3000)/5000)
    if _num(raw.get('chipset_tier')):v['ref_chipset']=_clip(raw['chipset_tier'])
    if _num(raw.get('screen_size_inch')) and _num(target):v['ref_screen']=_clip(100*(1-abs(raw['screen_size_inch']-target)/1.5))
    mp=camera_megapixels(camera)
    if mp is not None:
        v['ref_photo']=_clip(40*max(0,min(1,math.log2(mp/8)/math.log2(200/8)))
                             +30*('超广角' in camera)+30*('长焦' in camera or '潜望' in camera))
    if v['ref_chipset'] is not None and v['ref_battery'] is not None:
        v['ref_game']=_clip(.8*v['ref_chipset']+.2*v['ref_battery'])
    return v


def _camera_evidence(repo,candidates):
    records=repo.as_records(repo._q("SELECT * FROM specifications WHERE spec_key='rear_camera'"))
    sources={s['source_id']:s for s in repo.as_records(repo._q('SELECT source_id,url,publisher,published_at,collected_at FROM sources'))}
    result=[]
    for c in candidates:
        found=[]
        for kind,ident in (('sku',c['sku_id']),('variant',c['variant_id']),('model',c['model_id'])):
            found=[r for r in records if r['subject_type']==kind and r['subject_id']==ident]
            if found:break
        valid=[r for r in found if r.get('parse_status') in ('ok','non_numeric')
               and camera_megapixels(r.get('raw_value') or r.get('value_text')) is not None]
        texts={r.get('raw_value') or r.get('value_text') for r in valid}
        record=valid[0] if len(texts)==1 and len(valid)==len(found) else None
        ev={'metric_key':'rear_camera','status':'missing','value':None,'sources':[]}
        if record:
            refs=repo.get_spec_provenance(record['spec_id'])
            ids={r['source_id'] for r in refs if r.get('source_id')}
            if record.get('source_id'):ids.add(record['source_id'])
            ev={**record,'value':next(iter(texts)),'status':'spec_reference','spec_provenance':refs,
                'sources':[sources[k] for k in sorted(ids) if k in sources]}
        result.append(ev)
    return result


def build_spec_reference_input(repo,needs,weights,target,mode):
    requested=canonical_weights(weights)
    if any(v>0 and k not in METRICS for k,v in requested.items()):
        raise ValueError('规格参考模式仅支持七项偏好，价格和品牌请使用筛选条件')
    di=build_input(repo,needs,{k:1 for k in RAW},target,mode)
    chip_table,chip_sha=reference_table(repo)
    version=RULE_VERSION+'-'+chip_sha[:12]
    candidates=CatalogRepository.as_records(di['candidates'])
    cameras=_camera_evidence(repo,candidates)
    definitions=[{'metric_key':key,'name_cn':name,'unit':'分','direction':'benefit',
        'criterion_layer':'derived_reference','comparison_group_used':version,
        'cross_source_comparable':1,'value_type':'float','range_rule':'0—100；缺失为None'}
        for key,name,_ in METRICS.values()]
    matrix=[]; provenance=[]
    for i,c in enumerate(candidates):
        raw={d['metric_key']:di['X'][i][j] for j,d in enumerate(di['criterion_definitions'])}
        evidence={ev['metric_key']:deepcopy(ev) for ev in di['provenance'][i]['criteria']}
        chip=normalise_chipset(evidence['chipset_tier'].get('raw_value'))
        chip_entry=chip_table['entries'].get(chip)
        raw['chipset_tier']=chip_table['scores'].get(chip)
        evidence['chipset_tier'].update(value=raw['chipset_tier'],chipset_reference={
            'chipset':chip,'table_sha256':chip_sha,'entry':chip_entry,
            'source_snapshot':chip_table['source_snapshot']})
        if chip_entry and chip_entry.get('source_url'):
            evidence['chipset_tier']['sources'].append({'source_id':'socpk-overall','url':chip_entry['source_url'],
                'publisher':'极客湾 SOCPK','published_at':None,'collected_at':'2026-10-27'})
        scores=reference_values(raw,target,cameras[i]['value'])
        row=[]; facts=[]
        for source_key,(key,name,basis) in METRICS.items():
            if key in ('ref_chipset','ref_game'):
                status=(chip_entry or {}).get('evidence_status','missing')
                basis += '；芯片依据：'+{'primary_verified':'第一方历史榜单核验换算','inherited_estimate':'沿用旧表估算，未完成第一方核验',
                    'unmatched':'沿用旧表参考，具体变体未匹配第一方资料','missing':'缺少可匹配芯片档位'}[status]
            dependencies=([cameras[i]] if key=='ref_photo' else [evidence['chipset_tier'],evidence['battery_capacity_mah']]
                if key=='ref_game' else [evidence[source_key]])
            inputs=({'rear_camera':cameras[i]['value']} if key=='ref_photo' else
                {'chipset_tier':raw['chipset_tier'],'battery_capacity_mah':raw['battery_capacity_mah']}
                if key=='ref_game' else {source_key:raw[source_key]})
            if key=='ref_screen':inputs['target_screen']=target
            sources={s['source_id']:s for ev in dependencies for s in ev.get('sources',[])}
            fact={'metric_key':key,'name_cn':name,'layer':'derived_reference','value':scores[key],
                'unit':'分','comparison_group':version,'status':'derived_reference' if scores[key] is not None else 'missing',
                'estimated':True,'basis':basis,'raw_inputs':inputs,'raw_provenance':dependencies,
                'sources':list(sources.values()),'rule_version':version}
            if scores[key] is None:fact['missing_reason']='规格缺失或冲突；未猜测或填0'
            facts.append(fact);row.append(scores[key]); c[key]=scores[key]
        matrix.append(row);provenance.append({'sku_id':c['sku_id'],'criteria':facts})
    weights={ref[0]:requested.get(k,0) for k,ref in METRICS.items()}
    mask=[[v is None for v in row] for row in matrix]
    active=[j for j,d in enumerate(definitions) if weights[d['metric_key']]>0]
    eligible=[i for i,row in enumerate(mask) if all(not row[j] for j in active)]
    models={candidates[i]['model_id'] for i in eligible}
    status='no_candidates' if not candidates else 'insufficient_comparable_data' if len(models)<2 else 'ready'
    from .repository import pd
    di.update(candidates=pd.DataFrame(candidates),X=matrix,criterion_definitions=definitions,weights=weights,
        missing_mask=mask,provenance=provenance,eligible_indices=eligible,
        backup_indices=[i for i in range(len(candidates)) if i not in eligible],selected_run_ids=[{} for _ in candidates],
        comparison_groups={d['metric_key']:version for d in definitions},decision_status=status,
        decision_reason='规格估算仅供参考；缺失维度不编造分数' if status=='ready' else '硬约束下可评分的独立机型不足2个',
        coverage_summary={'candidate_count':len(candidates),'independent_model_count':len({c['model_id'] for c in candidates}),
            'eligible_sku_count':len(eligible),'eligible_model_count':len(models),
            'valid_cells_by_metric':{d['metric_key']:sum(not row[j] for row in mask) for j,d in enumerate(definitions)}})
    di['selection_rules'].update(evaluation_mode='spec_reference',required_transform=[],
        chipset_table={'version':chip_table['version'],'sha256':chip_sha,'source_snapshot':chip_table['source_snapshot'],
            'counts':chip_table['counts'],'requires_review':chip_table['requires_review']},
        group_selection='固定规格代理规则；不与实测成绩混合',reference_scoring={
            'version':version,'requested_weights':requested,'formulas':{k:b for k,_,b in METRICS.values()},
            'chipset_table_sha256':chip_sha,'chipset_counts':chip_table['counts'],'chipset_source':chip_table['source_snapshot'],
            'clipping':'0—100','game_components':{'ref_chipset':.8,'ref_battery':.2},
            'calibration':'初始设计，未由真实用户实验验证','missing_rule':'None与有效0分严格区分'})
    return di


def card_scores(di,sku_id):
    if di['selection_rules'].get('evaluation_mode')!='spec_reference':return None
    row=next(r for r in di['provenance'] if r['sku_id']==sku_id)
    return [{'metric_key':f['metric_key'],'name_cn':f['name_cn'],'score':f['value'],'unit':'分',
             'basis':f['basis'],'inputs':f['raw_inputs'],'estimated':True} for f in row['criteria']]
