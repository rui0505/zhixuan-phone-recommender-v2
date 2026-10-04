"""Create a reproducible pilot and a pending human checklist from registered IDs."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'data_pipeline'), str(ROOT / 'data_pipeline/src')]
from build_dataset import read_rows
from common import load_config
from stage_ids import select_pilot_ids


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    cfg = load_config(str(args.config))
    rows = read_rows(str(Path(cfg['source_dir']) / cfg['inputs']['price_main']), cfg['primary_sheet'], 'price_main')
    seed = (args.config.parent / cfg['registry_seed']).resolve()
    with seed.open(encoding='utf-8-sig', newline='') as fh:
        registry = list(csv.DictReader(fh))
    ids = {r['source_record_id']: r['id'] for r in registry if r['id_type'] == 'sku'}
    for row in rows:
        row['_sku_id'] = ids.get(row['source_record_id'])
    selected = select_pilot_ids(rows, registry)
    out = ROOT / 'local_build'
    out.mkdir(exist_ok=True)
    (out / 'pilot_ids.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
    with (out / '人工抽查清单.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['sku_id', '产品名称', '运行内存', '存储', '版本/备注', 'source_record_id', 'human_review_status'])
        for row in rows:
            if row['_sku_id'] in selected:
                writer.writerow([row['_sku_id'], row['产品名称'], row['运行内存'], row['存储'],
                                 row.get('版本/备注'), row['source_record_id'], 'NOT_CHECKED'])
    print(f'Prepared {len(selected)} registered SKUs; human review remains NOT_CHECKED')
