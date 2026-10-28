"""Strict, side-effect-free validation for draft and submitted phone intake.

Invalid payloads return ``(None, errors, warnings)``. Accepted payloads have a
fixed JSON shape, including explicit nulls for unknown numeric/source fields.
No URL is fetched and no queue/catalog storage is touched here.
"""
from datetime import date
import ipaddress
import math
import re
import unicodedata
from urllib.parse import unquote, urlsplit


PRICE_KINDS = frozenset({'official_current_price', 'marketplace_listing_price', 'used_market_estimate'})
CONDITIONS = frozenset({'new', 'used', 'unknown'})
# This is the publishable allowlist in scripts/export_snapshot.py. Scores and
# recommendation judgements deliberately have no intake field.
TECHNICAL_SPECS = frozenset({
    'battery_capacity_mah', 'chipset', 'display_peak_brightness_nit', 'front_camera',
    'has_headphone_jack', 'has_ir_blaster', 'has_nfc', 'has_wireless_charge', 'rear_camera',
    'refresh_rate_hz', 'resolution', 'screen_form', 'screen_material', 'screen_size_inch',
    'speaker_config', 'weight_g', 'wired_charge_w', 'wireless_charge_w',
})
MAX_SKUS = 20
MAX_SPECIFICATIONS = 40

_MISSING = object()
_FIELDS = {
    'body': {'model', 'variant', 'skus', 'specifications', 'source_note'},
    'model': {'brand', 'display_name', 'release_month', 'source_url'},
    'variant': {'variant_name', 'region', 'satellite_support', 'is_default'},
    'sku': {'ram_gb', 'storage_gb', 'memory_type', 'price'},
    'price': {'amount', 'price_kind', 'condition', 'observed_at', 'source_url', 'source_note'},
    'spec': {'key', 'value_num', 'unit', 'raw_value'},
}
_PRIVATE = re.compile(
    r'(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\\\s]+[\\/]|'
    r'/(?:Users|home|root|mnt|var)/|file://|%USERPROFILE%|'
    r'sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{16,}|github_pat_[a-z0-9_]{16,}|'
    r'\bAKIA[A-Z0-9]{16}\b|\bbearer\s+\S{12,}|'
    r'(?:api[_ -]?key|password|passwd|access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|secret|token|密钥|密码)\s*[=:])'
)
_HOST_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.I)
_DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_MONTH_RE = re.compile(r'[0-9]{4}-(?:0[1-9]|1[0-2])')

# key -> (canonical unit, accepted input units, inclusive lower/upper bound).
# These are generous sanity bounds, not claims of a measured score or model.
_NUMERIC_SPECS = {
    'battery_capacity_mah': ('mAh', {'mah', '毫安时'}, 1, 30000),
    'weight_g': ('g', {'g', '克'}, 1, 1000),
    'screen_size_inch': ('英寸', {'英寸', 'inch', 'in', 'inches', '"'}, 1, 20),
    'display_peak_brightness_nit': ('nit', {'nit', 'nits', '尼特'}, 0, 20000),
    'refresh_rate_hz': ('Hz', {'hz', '赫兹'}, 0, 1000),
    'wired_charge_w': ('W', {'w', '瓦'}, 0, 1000),
    'wireless_charge_w': ('W', {'w', '瓦'}, 0, 500),
}
_FLAG_SPECS = frozenset({'has_headphone_jack', 'has_ir_blaster', 'has_nfc', 'has_wireless_charge'})
_CORE_SPECS = frozenset({'weight_g', 'screen_size_inch', 'battery_capacity_mah'})


def _object(value, kind, label, errors, *, optional=False):
    if value is _MISSING and optional:
        return {}
    if type(value) is not dict:
        errors.append(f'{label}必须是 JSON 对象')
        return {}
    if any(type(key) is not str or key not in _FIELDS[kind] for key in value):
        # Do not echo untrusted unknown field names (which can contain secrets
        # or unpaired surrogates) into a response or log.
        errors.append(f'{label}存在未支持的字段')
    return value


def _invalid_characters(value):
    return '\ufffd' in value or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in value)


def _text(value, label, errors, *, required=False, maximum=256, preserve=False):
    if value is None or value is _MISSING:
        if required:
            errors.append(f'请填写{label}')
        return ''
    if type(value) is not str:
        errors.append(f'{label}必须是文本')
        return ''
    if len(value) > maximum:
        errors.append(f'{label}不能超过 {maximum} 个字符')
        return ''
    if _invalid_characters(value):
        errors.append(f'{label}含不可用字符')
        return ''
    if _PRIVATE.search(value):
        errors.append(f'{label}含私有路径或疑似密钥，请改用可公开资料')
        return ''
    if required and not value.strip():
        errors.append(f'请填写{label}')
    return value if preserve else value.strip()


def _number(value, label, errors, *, minimum=0, maximum=1000000, integer=False, required=False):
    if value is None or value is _MISSING:
        if required:
            errors.append(f'请填写{label}')
        return None
    if type(value) not in (int, float):
        errors.append(f'{label}必须是数字')
        return None
    # Check floats before conversion and integers before float(). This also
    # avoids OverflowError for arbitrarily large JSON integer values.
    if type(value) is float and not math.isfinite(value):
        errors.append(f'{label}必须是有限数字')
        return None
    if not minimum <= value <= maximum:
        errors.append(f'{label}须在 {minimum} 至 {maximum} 之间')
        return None
    if integer and type(value) is float and not value.is_integer():
        errors.append(f'{label}必须是整数')
        return None
    return int(value) if integer else float(value)


def _boolean(value, label, errors, *, default):
    if value is _MISSING:
        return default
    if type(value) is not bool:
        errors.append(f'{label}必须是布尔值')
        return default
    return value


def _date(value, label, errors, *, month=False, required=False):
    value = _text(value, label, errors, required=required, maximum=10)
    if not value:
        return None
    try:
        if not (_MONTH_RE if month else _DATE_RE).fullmatch(value):
            raise ValueError
        date.fromisoformat(value + '-01' if month else value)
    except ValueError:
        errors.append(f'{label}须为有效日期，格式 {"YYYY-MM" if month else "YYYY-MM-DD"}')
        return None
    return value


def _url(value, label, errors, *, required=False):
    value = _text(value, label, errors, required=required, maximum=2048)
    if not value:
        return None
    try:
        if any(char.isspace() for char in value) or re.search(r'%(?![0-9a-fA-F]{2})', value):
            raise ValueError
        decoded = unquote(value, errors='strict')
        if _invalid_characters(decoded) or _PRIVATE.search(decoded):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {'http', 'https'} or not host or
                parsed.username is not None or parsed.password is not None or '@' in parsed.netloc):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        if '%' in host:
            raise ValueError
        if ':' in host:
            ipaddress.IPv6Address(host)
            if not re.fullmatch(r'\[[^\]]+\](?::[0-9]+)?', parsed.netloc):
                raise ValueError
        else:
            if ':' in parsed.netloc and not re.fullmatch(r'[^:]+:[0-9]+', parsed.netloc):
                raise ValueError
            ascii_host = host.rstrip('.').encode('idna').decode('ascii')
            if len(ascii_host) > 253 or not all(_HOST_LABEL.fullmatch(part) for part in ascii_host.split('.')):
                raise ValueError
    except (ValueError, UnicodeError):
        errors.append(f'{label}必须是无用户凭据、端口和主机有效的 http 或 https 链接')
        return None
    return value


def _enum(value, label, allowed, errors, *, required=False):
    value = _text(value, label, errors, required=required, maximum=64)
    if value and value not in allowed:
        errors.append(f'{label}不受支持')
    return value or None


def _rows(value, label, maximum, errors, *, required=False):
    if value is _MISSING and not required:
        return []
    if type(value) is not list:
        errors.append(f'{label}必须是列表')
        return []
    if len(value) > maximum:
        errors.append(f'{label}不能超过 {maximum} 条')
        return []
    if required and not value:
        errors.append(f'至少添加一条{label}')
    return value


def _specification(item, index, errors, warnings):
    label = f'第 {index} 条规格'
    item = _object(item, 'spec', label, errors)
    key = _text(item.get('key'), label + '名称', errors, required=True, maximum=128)
    raw = _text(item.get('raw_value'), label + '原文', errors, required=True, maximum=500, preserve=True)
    unit = _text(item.get('unit'), label + '单位', errors, maximum=32) or None
    if key not in TECHNICAL_SPECS:
        errors.append(label + '名称不在可发布技术规格范围内')
    value = item.get('value_num')
    if key in _NUMERIC_SPECS:
        canonical, accepted, minimum, maximum = _NUMERIC_SPECS[key]
        value = _number(value, label + '数值', errors, minimum=minimum, maximum=maximum)
        if unit is not None:
            if unit.casefold() not in accepted:
                errors.append(label + '单位与规格名称不匹配')
            else:
                unit = canonical
        elif value is not None:
            # The key already defines the unit. Inferring this unit changes no
            # measured value; incompatible supplied units are always errors.
            unit = canonical
        if key in _CORE_SPECS and value is None:
            warnings.append(label + '数值未知，保留 null；展示时应标记未知')
    elif key in _FLAG_SPECS:
        value = _number(value, label + '数值', errors, minimum=0, maximum=1, integer=True)
        if unit not in (None, '-'):
            errors.append(label + '开关类型不应带物理单位')
        unit = None
    else:
        if value is not None:
            # Text-only specifications cannot acquire a fabricated numeric
            # interpretation, even when their raw text contains digits.
            _number(value, label + '数值', errors)
            errors.append(label + '仅支持原文，数值应为 null')
        value = None
        if unit not in (None, '-'):
            errors.append(label + '文字类型不应带物理单位')
        unit = None
    return {'key': key, 'value_num': value, 'unit': unit, 'raw_value': raw}


def _model_identity(model):
    # Catalog rows can be dictionaries or sqlite3.Row instances.
    try:
        brand, name = model['brand'], model['display_name']
    except (KeyError, IndexError, TypeError):
        return None
    if type(brand) is not str or type(name) is not str or not brand.strip() or not name.strip():
        return None
    return tuple(' '.join(unicodedata.normalize('NFKC', text).split()).casefold() for text in (brand, name))


def normalize_payload(body, existing_rows=None, pending_records=None, *, complete=False):
    """Return ``(normalized | None, errors: list[str], warnings: list[str])``.

    Drafts still require a brand, model, variant and one valid RAM/storage
    combination. ``complete=True`` additionally requires a release month and
    every price's amount, kind, condition, observation date and source URL.
    Specifications need model.source_url when complete. Callers must exclude
    the record being edited from pending_records before invoking this function.
    """
    errors, warnings = [], []
    if type(body) is not dict:
        return None, ['请求必须是 JSON 对象'], []
    _object(body, 'body', '请求', errors)
    model = _object(body.get('model', _MISSING), 'model', '机型', errors)
    variant = _object(body.get('variant', _MISSING), 'variant', '版本', errors)
    normalized = {
        'model': {
            'brand': _text(model.get('brand'), '品牌', errors, required=True),
            'display_name': _text(model.get('display_name'), '机型名称', errors, required=True),
            'release_month': _date(model.get('release_month'), '发布时间', errors, month=True, required=complete),
            'source_url': _url(model.get('source_url'), '规格来源链接', errors),
        },
        'variant': {
            'variant_name': _text(variant.get('variant_name'), '版本名称', errors, required=True),
            'region': _text(variant.get('region'), '销售地区', errors, maximum=128) or None,
            'satellite_support': _boolean(variant.get('satellite_support', _MISSING), '卫星功能', errors, default=False),
            'is_default': _boolean(variant.get('is_default', _MISSING), '默认版本', errors, default=True),
        },
        'skus': [], 'specifications': [],
        'source_note': _text(body.get('source_note'), '补充说明', errors, maximum=2000),
    }
    seen_skus = set()
    skus = _rows(body.get('skus', _MISSING), '配置', MAX_SKUS, errors, required=True)
    for index, sku in enumerate(skus, 1):
        label = f'第 {index} 个配置'
        sku = _object(sku, 'sku', label, errors)
        ram = _number(sku.get('ram_gb'), label + '运行内存', errors, minimum=1, maximum=128, integer=True, required=True)
        storage = _number(sku.get('storage_gb'), label + '存储容量', errors, minimum=1, maximum=16384, integer=True, required=True)
        if ram is not None and storage is not None:
            identity = (ram, storage)
            if identity in seen_skus:
                errors.append(label + '与前面的内存/存储组合重复')
            seen_skus.add(identity)
        price = _object(sku.get('price', _MISSING), 'price', label + '价格', errors, optional=True)
        normalized['skus'].append({
            'ram_gb': ram, 'storage_gb': storage,
            'memory_type': _text(sku.get('memory_type'), label + '内存类型', errors, maximum=128) or None,
            'price': {
                'amount': _number(price.get('amount'), label + '价格', errors, required=complete),
                'price_kind': _enum(price.get('price_kind'), label + '价格类型', PRICE_KINDS, errors, required=complete),
                'condition': _enum(price.get('condition'), label + '成色', CONDITIONS, errors, required=complete),
                'observed_at': _date(price.get('observed_at'), label + '记录日期', errors, required=complete),
                'source_url': _url(price.get('source_url'), label + '价格来源链接', errors, required=complete),
                'source_note': _text(price.get('source_note'), label + '价格说明', errors, maximum=500),
            },
        })
    seen_specs = set()
    specs = _rows(body.get('specifications', _MISSING), '规格', MAX_SPECIFICATIONS, errors)
    for index, item in enumerate(specs, 1):
        spec = _specification(item, index, errors, warnings)
        if spec['key'] in seen_specs:
            errors.append(f'第 {index} 条规格与前面的规格名称重复')
        seen_specs.add(spec['key'])
        normalized['specifications'].append(spec)
    if specs and not normalized['model']['source_url']:
        if complete:
            errors.append('填写技术规格时必须提供机型的规格来源链接')
        else:
            warnings.append('草稿缺少规格来源链接；提交前必须补齐')

    identity = _model_identity(normalized['model'])
    if identity:
        if any(_model_identity(row) == identity for row in (existing_rows or [])):
            errors.append('目录中已经存在同品牌同机型；新增配置请走补充配置流程')
        for record in pending_records or []:
            if type(record) is not dict:
                continue
            payload = record.get('payload')
            candidate = payload if type(payload) is dict else record
            if _model_identity(candidate.get('model')) == identity:
                errors.append('待发布队列中已经存在同品牌同机型，请先处理已有记录')
                break
    if not complete:
        if normalized['model']['release_month'] is None:
            warnings.append('草稿缺少发布时间；提交前必须补齐')
        for field, label in [('amount', '价格'), ('price_kind', '价格类型'), ('condition', '成色'),
                             ('observed_at', '记录日期'), ('source_url', '价格来源链接')]:
            if any(sku['price'][field] is None for sku in normalized['skus']):
                warnings.append(f'草稿缺少{label}；提交前必须补齐')
    return (None if errors else normalized), errors, warnings
