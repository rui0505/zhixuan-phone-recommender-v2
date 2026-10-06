"""Synthetic API releases; never depend on private workbooks or mutate production."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from tests.catalog_fixture import create_catalog, insert
from scripts.export_snapshot import export_snapshot


def make_release(root: Path, *, prepare=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source.sqlite'
    with closing(create_catalog(source)) as con:
        for n, refresh in ((1, 90), (2, 120)):
            insert(con, 'specifications', spec_id=f'refresh{n}', subject_type='sku',
                   subject_id=f'S{n}', spec_key='refresh_rate_hz', value_num=refresh,
                   raw_value=str(refresh), parse_status='ok', comparison_group='spec::refresh_rate_hz')
        if prepare:
            prepare(con)
        con.commit()
    destination = root / 'data' / 'catalog.sqlite'
    export_snapshot(source, destination, approved_asset_ids=set(), release_status={
        'machine_acceptance': 'PASS', 'overall_acceptance': 'PENDING', 'wps_review': 'NOT_CHECKED'})
    return destination


def request_payload(release, **updates):
    payload = dict(needs={'budget':{'min':None,'max':None},'min_storage':None,
                          'brands':{'include':[],'exclude':[]}},
                   weights={'price_cny':5,'weight_g':2,'storage_gb':2,'refresh_rate_hz':1,
                            'screen_size_inch':0,'dxomark_photo':0,'honor_of_kings_avg_fps':0},
                   target_screen=6.5,mode='new',confirmed=True,expected_release=deepcopy(release.key))
    payload.update(deepcopy(updates))
    return payload
