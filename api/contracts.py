"""Strict boundary contracts; no ignored fields or coercion of JSON booleans."""
from copy import deepcopy
from dataclasses import dataclass
import math
import re

from catalog.adapter import resolve_brands

METRICS = ('price_cny','weight_g','storage_gb','refresh_rate_hz','screen_size_inch',
           'dxomark_photo','honor_of_kings_avg_fps','chipset_tier')
DEFAULT_WEIGHTS = dict(zip(METRICS, (5,2,2,1,0,0,0,0)))
RELEASE_FIELDS = {'dataset_version','feature_rule_version','snapshot_sha256','manifest_sha256'}
REQUEST_FIELDS = {'needs','weights','target_screen','mode','confirmed','expected_release'}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class RecommendationRequest:
    needs: dict
    weights: dict[str,float]
    target_screen: float | None
    mode: str
    confirmed: bool
    expected_release: dict[str,str]


@dataclass(frozen=True)
class Pagination:
    page: int = 1
    page_size: int = 24


@dataclass(frozen=True)
class CatalogQuery:
    needs: dict
    mode: str
    q: str
    pagination: Pagination
    category: str = 'all'
    category_status: str = 'evidence_available'


def invalid(message='条件格式无效，请检查后重新确认'):
    raise ApiError(422,'invalid_request',message)


def _object(value, allowed, *, required=()):
    if type(value) is not dict or set(value).difference(allowed) or set(required).difference(value):
        invalid()
    return value


def _real(value, *, minimum=0, maximum=None, nullable=False, positive=False):
    if value is None and nullable:
        return None
    try:
        valid = (type(value) in (int,float) and math.isfinite(value) and value >= minimum
                 and (maximum is None or value <= maximum) and (not positive or value > 0))
    except OverflowError:
        valid = False
    if not valid:
        invalid('数值必须在允许范围内，不能为布尔值或非有限数')
    return float(value)


def _mode(mode):
    if type(mode) is not str or mode not in ('new','used','all'):
        invalid('请选择新机、二手或全部成色')
    return mode


def _brand_list(values, known):
    if type(values) is not list or any(type(v) is not str or not v or len(v)>128 for v in values):
        invalid('品牌必须来自可选目录')
    try:
        return resolve_brands(values,known)
    except ValueError as exc:
        raise ApiError(422,'invalid_request','品牌无法唯一匹配，请重新选择') from exc


def _needs(value, known):
    obj = _object(value, {'budget','min_storage','brands'}, required={'budget','min_storage','brands'})
    budget = _object(obj['budget'], {'min','max'}, required={'min','max'})
    lo, hi = (_real(budget[key],nullable=True) for key in ('min','max'))
    if lo is not None and hi is not None and lo > hi:
        invalid('预算下限不能高于上限')
    brands = _object(obj['brands'], {'include','exclude'}, required={'include','exclude'})
    include, exclude = (_brand_list(brands[key],known) for key in ('include','exclude'))
    if set(include).intersection(exclude):
        invalid('同一品牌不能同时包含和排除')
    return {'budget':{'min':lo,'max':hi},'min_storage':_real(obj['min_storage'],nullable=True,positive=True),
            'brands':{'include':include,'exclude':exclude}}


def _hash(value):
    if type(value) is not str or not re.fullmatch(r'[a-fA-F0-9]{64}',value):
        invalid('发布或决策指纹格式无效')
    return value


def parse_recommendation(body: dict, known_brands: list[str]) -> RecommendationRequest:
    obj = _object(body,REQUEST_FIELDS,required=REQUEST_FIELDS)
    if obj['confirmed'] is not True:
        invalid('请先检查条件、权重和数据限制并确认')
    weights = _object(obj['weights'],set(METRICS))
    if not weights:
        invalid('至少一项偏好权重必须大于 0')
    canonical = {k:_real(weights[k],maximum=10) for k in METRICS if k in weights}
    if not any(canonical.values()):
        invalid('至少一项偏好权重必须大于 0')
    target = _real(obj['target_screen'],minimum=3,maximum=10,nullable=True)
    if canonical.get('screen_size_inch',0)>0 and target is None:
        invalid('屏幕指标启用时必须确认目标尺寸')
    release = _object(obj['expected_release'],RELEASE_FIELDS,required=RELEASE_FIELDS)
    for key in ('dataset_version','feature_rule_version'):
        if type(release[key]) is not str or not release[key] or len(release[key])>256:
            invalid('发布版本格式无效')
    for key in ('snapshot_sha256','manifest_sha256'):
        _hash(release[key])
    return RecommendationRequest(_needs(obj['needs'],known_brands),canonical,target,
                                 _mode(obj['mode']),True,deepcopy(release))


def _queries(items, allowed, repeat=()):
    out = {}
    for key,value in items:
        if key not in allowed or type(value) is not str or (key in out and key not in repeat):
            invalid('查询字段未知、重复或格式无效')
        if key in repeat:
            out.setdefault(key,[]).append(value)
        else:
            out[key]=value
    return out


def _pagination(query):
    def integer(key,default,max_value=None):
        value=query.get(key,str(default))
        if not re.fullmatch(r'[0-9]{1,9}',value):
            invalid('页码和每页数量必须为正整数')
        number=int(value)
        if number<1 or (max_value is not None and number>max_value):
            invalid('分页超出允许范围')
        return number
    return Pagination(integer('page',1),integer('page_size',24,100))


def parse_pagination(items: list[tuple[str,str]]) -> Pagination:
    return _pagination(_queries(items,{'page','page_size'}))


def parse_catalog_query(items: list[tuple[str,str]], known_brands: list[str]) -> CatalogQuery:
    query=_queries(items,{'mode','min_price','max_price','min_storage','include','exclude','q','page','page_size','category','category_status'},
                   repeat={'include','exclude'})
    category=query.get('category','all')
    category_status=query.get('category_status','evidence_available')
    if category not in ('all','gaming','senior','photography'):
        invalid('请选择有效的手机场景分类')
    if category_status not in ('evidence_available','pending_review'):
        invalid('请选择有资料或待评估状态')
    if category=='all' and 'category_status' in query:
        invalid('请先选择具体场景，再筛选资料状态')
    def numeric(key):
        if key not in query:
            return None
        if not query[key].strip():
            invalid('筛选数值不能为空')
        try:
            return float(query[key])
        except (ValueError,OverflowError):
            invalid('筛选数值格式无效')
    needs=_needs({'budget':{'min':numeric('min_price'),'max':numeric('max_price')},
                  'min_storage':numeric('min_storage'),
                  'brands':{'include':query.get('include',[]),'exclude':query.get('exclude',[])}},known_brands)
    return CatalogQuery(needs,_mode(query.get('mode','all')),query.get('q','').strip(),_pagination(query),category,category_status)


def parse_compare(body: dict) -> list[str]:
    ids=_object(body,{'sku_ids'},required={'sku_ids'})['sku_ids']
    if (type(ids) is not list or not 1<=len(ids)<=4
            or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in ids)
            or len(set(ids))!=len(ids)):
        invalid('请选择一至四个不重复的有效配置 ID')
    return list(ids)


def parse_export(body: dict, known_brands: list[str]) -> tuple[RecommendationRequest,dict[str,str]]:
    obj=_object(body,{'request','fingerprints'},required={'request','fingerprints'})
    fps=_object(obj['fingerprints'],{'decision_sha256','ranking_sha256'},required={'decision_sha256','ranking_sha256'})
    return parse_recommendation(obj['request'],known_brands),{k:_hash(v) for k,v in fps.items()}
