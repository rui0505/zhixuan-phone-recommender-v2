"""Candidate-scoped, evidence-preserving input adapter; no ranking algorithm here."""
import math
from numbers import Real
from collections import defaultdict
from .comparability import game_group, select_game_runs, applicable_run, APPROVED_MAPPINGS
from .contracts import DecisionInput


def build_decision_input(repo, metric_keys, mode, weights, max_price, min_storage_gb,
                         brands, price_kind_preference, *, min_price=None, excluded_brands=None,
                         comparison_group_overrides=None) -> DecisionInput:
    from .repository import _validate_brand_list
    if metric_keys is None:
        metric_keys = ['honor_of_kings_avg_fps', 'dxomark_photo', 'display_peak_brightness_nit']
    if not isinstance(metric_keys, (list, tuple)) or not metric_keys or not all(isinstance(k, str) for k in metric_keys):
        raise ValueError('metric_keys 必须为非空指标名称列表')
    if len(set(metric_keys)) != len(metric_keys):
        raise ValueError('同一指标不能重复计权')
    _validate_brand_list('brands', brands)
    _validate_brand_list('excluded_brands', excluded_brands)
    if comparison_group_overrides:
        if not isinstance(comparison_group_overrides, dict) or any(
                key not in metric_keys or value not in APPROVED_MAPPINGS
                for key, value in comparison_group_overrides.items()):
            raise ValueError('比较组覆盖必须引用已审核的映射ID；当前无已审核跨来源映射')

    definitions = {d['metric_key']: d for d in repo.as_records(repo.get_metric_definitions())}
    specs = repo.as_records(repo._q('SELECT * FROM specifications ORDER BY spec_id'))
    spec_keys = {s['spec_key'] for s in specs}
    # 文本规格不转换为虚构的数值级别；未来需新增明确定义才能入矩阵。
    numeric_specs = {'weight_g', 'screen_size_inch', 'refresh_rate_hz', 'battery_capacity_mah',
                     'has_nfc', 'has_ir_blaster', 'has_headphone_jack', 'has_wireless_charge',
                     'wired_charge_w', 'wireless_charge_w', 'display_peak_brightness_nit'}
    virtual_keys = {'price_cny', 'storage_gb'}
    for key in metric_keys:
        if key == 'dxomark_normalized_100' or (key in definitions and not definitions[key]['cross_source_comparable']):
            raise ValueError(f'{key} 仅用于原文展示，不得进入排名矩阵；不能靠归一化混合不同协议')
        if key in spec_keys and key not in numeric_specs:
            raise ValueError(f'{key} 是文本规格，不是已定义的数值决策指标')
        if key not in definitions and key not in numeric_specs.intersection(spec_keys) and key not in virtual_keys:
            raise ValueError(f'未知决策指标：{key}')
    if weights is None:
        weights = {k: 1.0 for k in metric_keys}
    if not isinstance(weights, dict) or set(weights).difference(metric_keys):
        raise ValueError('weights 必须为所选指标的权重字典，不能含未选指标')
    unnormalized = {k: weights.get(k, 0.0) for k in metric_keys}
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or v < 0
           for v in unnormalized.values()):
        raise ValueError('权重必须是有限的非负数值，不能用布尔值')
    # 先缩放避免极大有限权重之和溢出。
    scale = max(unnormalized.values())
    if scale <= 0:
        raise ValueError('至少一个指标权重必须大于 0')
    scaled = {k: float(v / scale) for k, v in unnormalized.items()}
    total = sum(scaled.values())
    w = {k: v / total for k, v in scaled.items()}
    if w.get('dxomark_overall', 0) > 0 and any(w.get(k, 0) > 0 for k in
            ('dxomark_photo','dxomark_video','dxomark_zoom','dxomark_lowlight')):
        raise ValueError('DXOMARK 总分与其子分不能重复计权，请选择总分或子分')

    candidates = repo.as_records(repo.load_catalog(mode=mode, min_storage_gb=min_storage_gb,
                                                   max_price=max_price, min_price=min_price,
                                                   excluded_brands=excluded_brands,
                                                   price_kind_preference=price_kind_preference))
    if brands is not None:
        candidates = [c for c in candidates if c['brand'] in brands]
    model_ids = {c['model_id'] for c in candidates}
    observations = repo.as_records(repo._q(
        'SELECT o.*, r.model_id, r.subject_type, r.subject_id, r.variant_id, r.sku_id, '
        'r.protocol, r.protocol_detail, r.run_date, r.institution, r.benchmark, '
        'r.applicable_scope, r.scope_note, r.conditions_json, r.source_id, r.source_record_id AS run_source_record_id '
        'FROM metric_observations o JOIN benchmark_runs r ON r.run_id=o.run_id '
        'ORDER BY o.observation_id'))
    by_obs, by_spec = defaultdict(list), defaultdict(list)
    for o in observations:
        if o['model_id'] in model_ids:
            by_obs[(o['model_id'], o['metric_key'])].append(o)
    for s in specs:
        by_spec[(s['subject_type'], s['subject_id'], s['spec_key'])].append(s)

    def applicable_specs(cand, key):
        for typ, ident in (('sku', cand['sku_id']), ('variant', cand['variant_id']), ('model', cand['model_id'])):
            found = by_spec[(typ, ident, key)]
            if found:
                return found  # 有高优先级的缺失记录也不能退回错误的通用值。
        return []

    # Assess hardware scope against ALL configurations, not just the filtered sample.
    all_skus = repo.as_records(repo._q('SELECT sku_id,variant_id,model_id FROM skus'))
    model_chips, variant_chips = defaultdict(set), defaultdict(set)
    for c in all_skus:
        chip = applicable_specs(c, 'chipset')
        signature = tuple(sorted(str(s.get('raw_value')) for s in chip)) or ('unknown',)
        model_chips[c['model_id']].add(signature)
        variant_chips[c['variant_id']].add(signature)
    for c in candidates:
        c['_model_hardware_conflict'] = len(model_chips[c['model_id']]) > 1
        c['_variant_hardware_conflict'] = len(variant_chips[c['variant_id']]) > 1
    game_keys = [k for k in metric_keys if k.startswith('honor_of_kings_') and w[k] > 0]
    game_runs = list({o['run_id']:o for o in observations if o['metric_key'].startswith('honor_of_kings_')}.values())
    game_selection = select_game_runs(game_runs, observations, game_keys, candidates, APPROVED_MAPPINGS) if game_keys else None

    def group_of(record, key, layer):
        base = record.get('comparison_group') or ''
        if not base or any(t in base.lower() for t in ('unknown', 'not_comparable', '未查到', '未公开')):
            return None
        if layer == 'measured' and key.startswith('dxomark_') and base not in ('dxomark_v4', 'dxomark_v5', 'dxomark_v6'):
            return None  # 混写协议的原文不能推断为某一代。
        if layer == 'measured' and key.startswith('honor_of_kings_'):
            return game_group(record)[0]
        if key == 'display_peak_brightness_nit':
            # 窗口/全屏口径不明确的峰值只能展示。类别相同也不是条件相同。
            category = base.split('::')[-1]
            if category in ('unverified', 'unspecified', 'unspecified_peak', 'other'):
                return None
            return base + '|protocol=' + (record.get('note') or '').strip()
        return base

    criteria, chosen_groups, coverage, config_coverage = [], {}, {}, {}
    rows_per_criterion = {}
    for key in metric_keys:
        # 有数值规格的字段始终来自规格层，不能被只有缺失的观测层遮蔽。
        layer = 'virtual' if key in virtual_keys else 'spec' if key in numeric_specs and key in spec_keys else 'measured'
        definition = definitions.get(key, {})
        matching_specs = [s for s in specs if s['spec_key'] == key]
        unit = ('元' if key == 'price_cny' else 'GB') if layer == 'virtual' else definition.get('unit') or (matching_specs[0]['unit'] if matching_specs else '')
        criterion = {'metric_key': key, 'criterion_layer': layer,
                     'name_cn': definition.get('name_cn', key), 'unit': unit,
                     'direction': 'target' if key == 'screen_size_inch' else definition.get('direction', 'cost' if key in ('weight_g','price_cny') else 'benefit'),
                     'value_type': definition.get('value_type', 'float'),
                     'range_rule': definition.get('range_rule', '见字段字典'),
                     'cross_source_comparable': 1}
        model_sets, sku_sets, usable_rows = defaultdict(set), defaultdict(set), []
        for cand in candidates:
            if layer == 'virtual':
                found = [{'value_num':cand.get(key), 'comparison_group':'virtual::'+key,
                          'source_record_id':cand.get('price_source_record_id' if key == 'price_cny' else 'sku_source_record_id'),
                          'source_id':cand.get('price_source_id') if key == 'price_cny' else None,
                          'price_id':cand.get('price_id') if key == 'price_cny' else None,
                          'observed_at':cand.get('observed_at') if key == 'price_cny' else None,
                          'parse_status':'ok', 'subject_type':'sku', 'subject_id':cand['sku_id']}]
            else:
                found = applicable_specs(cand, key) if layer == 'spec' else by_obs[(cand['model_id'], key)]
            applicable = []
            for record in found:
                if layer == 'measured':
                    if not applicable_run(record, cand):
                        continue
                    if key in game_keys and record['run_id'] != game_selection['run_by_sku'].get(cand['sku_id']):
                        continue
                group = group_of(record, key, layer)
                value = record.get('value_num')
                if value is None or isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or group is None:
                    continue
                if layer == 'spec' and record.get('parse_status') != 'ok':
                    continue
                applicable.append((group, record))
                model_sets[group].add(cand['model_id'])
                sku_sets[group].add(cand['sku_id'])
            usable_rows.append((found, applicable))
        rows_per_criterion[key] = usable_rows
        # 统计独立机型，不能让同一实测的多存储配置增加其分组权重。
        coverage[key] = {g: len(ids) for g, ids in sorted(model_sets.items())}
        config_coverage[key] = {g: len(ids) for g, ids in sorted(sku_sets.items())}
        ordered = sorted(coverage[key], key=lambda g: (-coverage[key][g], g))
        chosen_groups[key] = ordered[0] if ordered else None
        if key in game_keys:
            chosen_groups[key] = game_selection['comparison_group']
            coverage[key] = game_selection['coverage']
        criterion.update(comparison_group_used=chosen_groups[key],
                         comparison_group_used_is_unverified=False,
                         comparison_group_coverage=coverage[key],
                         comparison_group_sku_coverage=config_coverage[key])
        criteria.append(criterion)

    X, masks, provenance, selected_run_ids = [], [], [], []
    for i, cand in enumerate(candidates):
        values, missing, evidence = [], [], []
        for criterion in criteria:
            key, layer = criterion['metric_key'], criterion['criterion_layer']
            found, applicable = rows_per_criterion[key][i]
            selected = [r for group, r in applicable if group == chosen_groups[key]]
            different_values = {r['value_num'] for r in selected}
            # 重复证据内容一致时取稳定首项。相同条件但分数冲突时不静默取首项。
            record = selected[0] if selected and len(different_values) == 1 else None
            value = record['value_num'] if record else None
            values.append(value)
            missing.append(record is None)
            ev = {'metric_key': key, 'layer': layer, 'value': value, 'unit': criterion['unit'],
                  'comparison_group': chosen_groups[key], 'status': 'missing'}
            if record:
                ev.update({k: record.get(k) for k in ('source_id', 'source_record_id', 'spec_id',
                          'run_id', 'protocol', 'protocol_detail', 'run_date', 'applicable_scope',
                          'scope_note', 'subject_type', 'subject_id', 'raw_value', 'parse_status',
                          'conditions_json', 'price_id', 'observed_at')})
                ev['status'] = 'measured' if layer == 'measured' else 'selected_price' if key == 'price_cny' else 'spec_reference'
                ev['protocol_raw'] = record.get('note')
                if layer == 'spec':
                    ev['note'] = '规格参考值，非实测表现；model 通用值仅已核对当前数据中的全部配置'
            else:
                reason = ('conflicting_comparable_values' if len(different_values) > 1 else
                          'different_comparison_group' if applicable else
                          'unverified_comparison_conditions' if any(r.get('value_num') is not None for r in found) else
                          next((r.get('missing_reason') for r in found if r.get('missing_reason')), 'no_record'))
                ev['missing_reason'] = reason
                ev['available_comparison_groups'] = sorted({g for g, _ in applicable})
            evidence.append(ev)
        X.append(values)
        masks.append(missing)
        provenance.append({'sku_id': cand['sku_id'], 'criteria': evidence})
        selected_run_ids.append({ev['metric_key']:ev.get('run_id') for ev in evidence if ev['layer'] == 'measured'})
    active = [j for j, key in enumerate(metric_keys) if w[key] > 0]
    eligible = [i for i, mask in enumerate(masks) if all(not mask[j] for j in active)]
    eligible_models = {candidates[i]['model_id'] for i in eligible}
    positive_keys = [metric_keys[j] for j in active]
    required_transform = [k for k in positive_keys if k == 'screen_size_inch']
    if not candidates:
        status, reason = 'no_candidates', '硬约束下没有候选配置，请放宽预算、存储或品牌条件'
    elif len(eligible_models) < 2:
        status, reason = 'insufficient_comparable_data', '重要指标同时有值且可比的独立机型少于 2 个；只展示目录和备选，不生成完整推荐'
    elif required_transform:
        status, reason = 'requires_target_transform', '屏幕尺寸必须先按用户目标计算偏差，不能直接按越大越好排序'
    else:
        status, reason = 'ready', '输入可供算法使用；本接口不计算排名或保证评测质量'
    release = repo.dataset_version()
    sources={s['source_id']:s for s in repo.as_records(repo._q(
        'SELECT source_id,url,publisher,published_at,collected_at FROM sources'))}
    for row in provenance:
        for ev in row['criteria']:
            ids={ev['source_id']} if ev.get('source_id') else set()
            if ev.get('spec_id'):
                ev['spec_provenance']=repo.get_spec_provenance(ev['spec_id'])
                ids.update(p['source_id'] for p in ev['spec_provenance'] if p.get('source_id'))
            ev['sources']=[sources[sid] for sid in sorted(ids) if sid in sources]
    from .repository import pd, PRICE_PRIORITY_NEW, PRICE_PRIORITY_USED
    for c in candidates:
        c.pop('_model_hardware_conflict', None)
        c.pop('_variant_hardware_conflict', None)
    return {'candidates': pd.DataFrame(candidates) if pd is not None else candidates,
            'X': X, 'criterion_definitions': criteria, 'weights': w,
            'missing_mask': masks, 'comparison_groups': chosen_groups, 'provenance': provenance,
            'dataset_version': release.get('dataset_version'), 'feature_rule_version': release.get('rule_version'),
            'decision_status': status, 'decision_reason': reason,
            'selected_run_ids': selected_run_ids,
            'eligible_indices': eligible, 'backup_indices': [i for i in range(len(candidates)) if i not in set(eligible)],
            'coverage_summary': {'candidate_count': len(candidates), 'independent_model_count': len(model_ids),
                                 'eligible_sku_count': len(eligible), 'eligible_model_count': len(eligible_models),
                                 'valid_cells_by_metric': {k: sum(not row[j] for row in masks) for j, k in enumerate(metric_keys)}},
            'selection_rules': {'mode': mode, 'min_price':min_price, 'max_price': max_price, 'min_storage_gb': min_storage_gb,
                                'brands': list(brands) if brands is not None else None,
                                'excluded_brands':list(excluded_brands) if excluded_brands is not None else None,
                                'one_row_per': 'sku_id', 'sku_in_mode': len(candidates),
                                'price_priority': PRICE_PRIORITY_NEW if mode == 'new' else PRICE_PRIORITY_USED if mode == 'used' else PRICE_PRIORITY_NEW + PRICE_PRIORITY_USED,
                                'custom_price_priority':list(price_kind_preference) if price_kind_preference is not None else None,
                                'game_selection':game_selection,
                                'weights_note': '无权重时试验等权；传入部分权重时其余为 0，再归一化。非行业共识',
                                'group_selection': '硬约束后按独立机型覆盖数选组；并列按分组键排序；不混合不同机构游戏测评',
                                'required_transform': required_transform,
                                'missing_rule': '缺失/不可比/冲突保持 None；仅正权重指标决定主推荐输入资格；必须检查 decision_status'}}
