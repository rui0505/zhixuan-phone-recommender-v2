"""Synthetic temporary catalog; no private workbook or prior built database needed."""
import ast
import json
import sqlite3
from pathlib import Path


def insert(con, table, **values):
    keys = ','.join(values)
    marks = ','.join('?' for _ in values)
    con.execute(f'INSERT INTO {table} ({keys}) VALUES ({marks})', tuple(values.values()))


def create_catalog(path):
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'data_pipeline/src/stage_db.py').read_text(encoding='utf-8'))
    ddl = next(ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DDL' for t in node.targets))
    con = sqlite3.connect(path)
    con.executescript(ddl)
    insert(con, 'dataset_releases', dataset_version='fixture-v1', rule_version='fixture-rule-v1')
    for n in (1, 2):
        insert(con, 'phone_models', model_id=f'M{n}', display_name=f'测试机{n}', display_name_match_key=f'm{n}', brand=f'品牌{n}')
        insert(con, 'phone_variants', variant_id=f'V{n}', model_id=f'M{n}', variant_name='标准', name_is_source=0)
        insert(con, 'skus', sku_id=f'S{n}', variant_id=f'V{n}', model_id=f'M{n}', ram_raw='12GB', ram_gb=12,
               storage_raw='256GB', storage_gb=256, source_record_id=f'CONFIG{n}')
        insert(con, 'price_snapshots', price_id=f'P{n}', sku_id=f'S{n}', amount=n*1000, currency='CNY',
               price_kind='official_current_price', condition='new', observed_at='2026-01-01', source_record_id=f'PRICE{n}')
        for key, val in (('weight_g', 190+n), ('battery_capacity_mah', 5000), ('screen_size_inch', 6.7)):
            insert(con, 'specifications', spec_id=f'{key}{n}', subject_type='sku', subject_id=f'S{n}',
                   spec_key=key, value_num=val, raw_value=str(val), parse_status='ok',
                   comparison_group=f'spec::{key}', source_record_id=f'CONFIG{n}')
    for key, direction in (('honor_of_kings_avg_fps', 'benefit'), ('honor_of_kings_power_w', 'cost'), ('dxomark_photo','benefit'), ('dxomark_overall','benefit')):
        insert(con, 'metric_definitions', metric_key=key, direction=direction, cross_source_comparable=1)
    con.commit()
    return con


def conditions(**updates):
    result = dict(game_version='v1', quality='极高', render_resolution='1920x1080', fps_target=120,
                  scene='固定对局', duration_min=30, ambient_temp_c=25, performance_mode='均衡',
                  cooler='无', measurement_method='固定采样', os_version='OS1')
    result.update(updates)
    return result


def game_run(con, model, run_id, metrics, **updates):
    insert(con, 'benchmark_runs', run_id=run_id, model_id=model, subject_type='model', subject_id=model,
           benchmark='王者荣耀', institution='测试机构', protocol='fixture-game-v1', comparison_group='legacy-group',
           conditions_json=json.dumps(conditions(**updates), ensure_ascii=False), applicable_scope='model', run_date='2026-01-01')
    for key, val in metrics.items():
        insert(con, 'metric_observations', observation_id=run_id+key, run_id=run_id, metric_key=key,
               value_num=val, comparison_group='legacy-group')
    con.commit()
