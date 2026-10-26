"""Bounded, server-only natural-language extraction into an unconfirmed draft."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading

from bridge import infer_budget_range
from needs_extraction import extract_needs, validate_result, budget_bound_has_source
from providers.jev import JevUnavailable, normalise_response
from providers.llm import SYSTEM_PROMPT, USER_TEMPLATE, post_chat, content_of, extract_json_object
from schemas import WEIGHT_TABLE
from .contracts import ApiError, METRICS, _needs

# 维度 → 矩阵指标。**必须显式写，不能用 METRICS 的位置切片**：
# CRITERIA 已经不含 price，按位置映射会让 weight 错位到 price_cny。
CONTROL_KEYS = {'weight':'weight_g','storage':'storage_gb','screen':'screen_size_inch',
                'photo':'dxomark_photo','game':'honor_of_kings_avg_fps',
                'battery':'battery_capacity_mah','chipset':'chipset_tier'}


class OfficialNeedsClassifier:
    """One attempt, fixed official endpoint; no user-selected gateway or retries."""
    def __init__(self, key):
        self.key = key

    def classify(self, text):
        model = os.environ.get('LLM_MODEL', 'deepseek-flash')
        payload = {'model':model, 'temperature':0, 'max_tokens':1100,
                   'thinking':{'type':'disabled'}, 'response_format':{'type':'json_object'},
                   'messages':[{'role':'system', 'content':SYSTEM_PROMPT},
                               {'role':'user', 'content':USER_TEMPLATE.format(text=text)}]}
        body, error = post_chat('https://api.deepseek.com/chat/completions', payload,
                                self.key, timeout=18, retries=0)
        if body is None:
            raise JevUnavailable('DeepSeek 本次不可用，请使用手动填写')
        return normalise_response(extract_json_object(content_of(body)), provider='deepseek',
                                  model_version=model, source_text=text)


class NeedsService:
    def __init__(self, *, enabled=None, key=None, classifier=None, quota_path=None, daily_limit=20):
        self.enabled = (os.environ.get('PHONE_ENABLE_LLM','').lower()=='true' and
                        os.environ.get('PHONE_ENABLE_NEEDS_EXTRACTION','').lower()=='true') if enabled is None else enabled
        self.key = os.environ.get('LLM_API_KEY','') if key is None else key
        self.classifier = classifier
        self.quota_path = quota_path or os.environ.get('PHONE_NEEDS_QUOTA_PATH')
        self.daily_limit = max(0, min(20, int(daily_limit)))
        self.lock = threading.Lock()

    @property
    def available(self):
        return bool(self.enabled and self.key and self.quota_path and self.daily_limit)

    def _reserve(self):
        # SQLite transaction persists attempts across restarts and release switches.
        day = datetime.now(timezone.utc).date().isoformat()
        with closing(sqlite3.connect(str(Path(self.quota_path)), timeout=2)) as con:
            con.execute('CREATE TABLE IF NOT EXISTS quota (day TEXT PRIMARY KEY, attempts INTEGER NOT NULL)')
            con.execute('BEGIN IMMEDIATE')
            count = con.execute('SELECT attempts FROM quota WHERE day=?', (day,)).fetchone()
            if count and count[0] >= self.daily_limit:
                raise ApiError(429, 'needs_daily_limit', '今日需求理解次数已用完，仍可手动填写并生成推荐')
            con.execute('INSERT INTO quota VALUES (?,1) ON CONFLICT(day) DO UPDATE SET attempts=attempts+1', (day,))
            con.commit()

    def extract(self, text, known_brands):
        if not self.available:
            raise ApiError(503, 'needs_unavailable', '自然语言理解暂不可用，请使用下方手动填写')
        if not self.lock.acquire(blocking=False):
            raise ApiError(429, 'needs_busy', '正在处理其他需求，请稍后重试或手动填写')
        try:
            try:
                self._reserve()
            except ApiError:
                raise
            except (OSError, sqlite3.Error):
                raise ApiError(503, 'needs_unavailable', '调用额度暂无法核验，请使用手动填写') from None
            result = extract_needs(text, classifier=self.classifier or OfficialNeedsClassifier(self.key))
            # Public output must never carry a raw upstream exception.
            if result['status']=='failed':
                result['meta']['error']='需求理解失败，请使用手动填写'
                return {'status':'failed', 'extraction':result, 'draft':None,
                        'notice':'本次未取得可靠偏好，请手动填写；没有生成排名'}
            try:
                for bound in ('min','max'):
                    value=result['budget'][bound]
                    if value is not None and not budget_bound_has_source(text,value,bound):
                        result['budget'][bound]=None
                        result['status']='degraded'
                        if not any(c['field']==f'budget.{bound}' for c in result['clarifications']):
                            result['clarifications'].append({'field':f'budget.{bound}',
                                'question':'预算上限是多少？也可以明确选择不限。' if bound=='max' else '是否需要设置预算下限？请在表单中确认。',
                                'reason':'模型给出了原文没有依据的预算边界，已清除'})
                if validate_result(result):
                    raise ValueError('invalid extraction')
                # 含糊预算（「5000左右」）：用户没有明确边界，但给了锚点。
                # 按非对称阶梯推断一个区间填进草稿，并在 notice 里明说是推断值——
                # 红线是"不得静默封顶"，不是"不得封顶"。既无锚点也无边界时留空。
                budget_min, budget_max = result['budget']['min'], result['budget']['max']
                inferred = None
                if budget_min is None and budget_max is None and result['budget'].get('reference') is not None:
                    inferred = infer_budget_range(result['budget']['reference'])
                    budget_min, budget_max = inferred[0], inferred[1]
                needs = _needs({'budget':{'min':budget_min,'max':budget_max},
                                'min_storage':result['min_storage'], 'brands':result['brands']}, known_brands)
                weights = dict.fromkeys(METRICS, 0)
                for pref in result['preferences']:
                    metric = CONTROL_KEYS.get(pref['criterion'])
                    if metric:
                        weights[metric] = WEIGHT_TABLE[pref['criterion']][pref['level']]
                if result['target_screen'] is None:
                    weights['screen_size_inch']=0
                if not any(weights.values()):
                    raise ValueError('no active preferences')
            except (ValueError, TypeError, KeyError, ApiError):
                return {'status':'degraded', 'extraction':result, 'draft':None,
                        'notice':'条件、品牌或偏好未能可靠匹配，请在下方手动确认；没有自动放宽条件'}
            notice='已生成待确认草稿。未提及的基础偏好使用系统默认1；拍照和游戏未自动计权，新机/二手请手动选择'
            if inferred is not None:
                notice=(f'你说的是「{result["budget"]["reference"]:g} 元左右」，'
                        f'已按 ¥{inferred[0]:,}–¥{inferred[1]:,} 推断预算范围（推断值，可修改）。'
                        + notice)
            return {'status':result['status'], 'extraction':result,
                    'draft':{'needs':needs, 'weights':weights, 'target_screen':result['target_screen'],
                             'confirmed':False, 'budget_inferred':inferred is not None},
                    'notice':notice}
        finally:
            self.lock.release()


def parse_needs_text(body):
    if (type(body) is not dict or set(body)!={'text'} or type(body['text']) is not str
            or not body['text'].strip() or len(body['text'])>2000):
        raise ApiError(422, 'invalid_request', '请填写1至2000字的购机需求，仅接受需求文本')
    return body['text'].strip()
