"""Exact SKU scope and safe provenance, without Streamlit or presentation state."""
from urllib.parse import urlsplit

from .repository import CatalogRepository


def safe_url(value: object) -> str | None:
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        if parsed.scheme in ('http', 'https') and parsed.hostname and parsed.username is None and parsed.password is None:
            return value
    except ValueError:
        pass
    return None


def safe_urls(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(url for line in value.splitlines() if (url := safe_url(line.strip()))))


def sku_details(repo: CatalogRepository, sku_id: str) -> dict | None:
    rows = repo.as_records(repo._q(
        'SELECT s.*,m.display_name,v.variant_name FROM skus s JOIN phone_models m USING(model_id) '
        'JOIN phone_variants v USING(variant_id) WHERE sku_id=?', (sku_id,)))
    if not rows:
        return None
    sku = rows[0]
    details = repo.get_model_details(model_id=sku['model_id'])
    details['sku'] = sku
    identities = {'sku': sku_id, 'variant': sku['variant_id'], 'model': sku['model_id']}
    details['specifications'] = [r for r in details['specifications'] if identities.get(r['subject_type']) == r['subject_id']]
    details['prices'] = [r for r in details['prices'] if r['sku_id'] == sku_id]
    return details


def sku_sources(repo: CatalogRepository, detail: dict) -> list[dict]:
    ids = {r.get('source_id') for group in ('specifications', 'prices', 'benchmark_runs')
           for r in detail[group] if r.get('source_id')}
    for spec in detail['specifications']:
        ids.update(p['source_id'] for p in repo.get_spec_provenance(spec['spec_id']) if p.get('source_id'))
    records = repo.as_records(repo._q('SELECT source_id,url,publisher FROM sources ORDER BY source_id'))
    return [dict(source_id=r['source_id'], publisher=r['publisher'], urls=safe_urls(r['url']))
            for r in records if r['source_id'] in ids]
