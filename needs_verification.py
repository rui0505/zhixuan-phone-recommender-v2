"""Independent, bounded source checks for the public needs draft. No model calls.

Rules cover explicit wording only. A matching source span is not proof of entailment;
unrecognised grounded preferences remain suggestions that need human review.
"""
from copy import deepcopy
import re

VERSION = 'needs-rules-v1'
NUMBER = r'(?:\d+(?:\.\d+)?\s*[万千]?|[零〇一二两三四五六七八九十百千万]+)'
DIGITS = {c: n for n, chars in enumerate(('零〇', '一', '二两', '三', '四', '五', '六', '七', '八', '九')) for c in chars}
SUBJECTS = {
    'weight': r'重量|轻便|轻一点|轻点|要轻|最轻|太重',
    'storage': r'存储|储存|存储空间',
    'screen': r'屏幕尺寸|屏幕大小|屏幕|尺寸',
    'photo': r'拍照|影像|相机|摄影',
    'game': r'游戏|王者荣耀|和平精英|原神|开黑',
    'battery': r'电池容量|大电池|电池(?:越|要|更)?大|毫安|mAh',
    'chipset': r'处理器|芯片',
}
SUBJECT_RE = re.compile('|'.join(f'(?P<{key}>{pattern})' for key, pattern in SUBJECTS.items()), re.I)
BRANDS = {
    '苹果': ('苹果', 'iPhone', 'Apple'), '小米': ('小米', 'Xiaomi'),
    'Redmi': ('Redmi', '红米'), '华为': ('华为', 'Huawei'), '荣耀': ('荣耀', 'Honor'),
    '三星': ('三星', 'Samsung'), 'vivo': ('vivo',), 'OPPO': ('OPPO',),
    '一加': ('一加', 'OnePlus'), 'iQOO': ('iQOO',), 'realme': ('realme', '真我'),
    '魅族': ('魅族', 'Meizu'),
}


def _number(token):
    token = re.sub(r'\s+', '', token)
    if re.fullmatch(r'\d+(?:\.\d+)?[万千]?', token):
        multiplier = {'万': 10000, '千': 1000}.get(token[-1], 1)
        return float(token[:-1] if multiplier != 1 else token) * multiplier
    # Do not interpret colloquial "四五千" or "三千五" as exact amounts.
    if re.search(r'[一二两三四五六七八九]{2}|[千万][一二两三四五六七八九]$', token):
        return None
    total, section, digit = 0, 0, 0
    for char in token:
        if char in DIGITS:
            digit = DIGITS[char]
        elif char == '万':
            total += (section + digit or 1) * 10000
            section, digit = 0, 0
        elif char in '十百千':
            section += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
        else:
            return None
    return float(total + section + digit)


def _turns(text):
    return [re.sub(r'^\s*\d+[.、]\s*', '', line).strip()
            for line in text.splitlines() if line.strip()]


def _clauses(text):
    return [part.strip() for part in re.split(r'[，,。；;！!？?\n]|但是|不过|但|而且|并且', text) if part.strip()]


def _claim(values):
    """(value, literal source, conflict); contradictory same-turn values stay unknown."""
    unique = {value for value, _ in values}
    evidence = '；'.join(dict.fromkeys(source for _, source in values))
    return (next(iter(unique)) if len(unique) == 1 else None,
            values[-1][1] if len(unique) == 1 else evidence, len(unique) > 1)


def _money_context(clause, match, *, allow_vague=False):
    after = clause[match.end():].lstrip()
    if re.match(r'(?:G(?:B)?|TB|英寸|寸|克|毫安|mAh)', after, re.I):
        return False
    if not allow_vague and re.match(r'(?:左右|上下|多|来|出头)', after):
        return False
    before = clause[:match.start()]
    if 'n' in match.groupdict() and _number(match['n']) in (32, 64, 128, 256, 512, 1024, 2048, 4096):
        if not re.search(r'元|块|预算|价格|价位', clause):
            return False
    return not (re.search(r'存储|内存|屏幕|尺寸|重量', before) and not re.search(r'预算|价格|价位', before))


def _budget_claims(text):
    claims = {}
    for turn in _turns(text):
        values = {'min': [], 'max': []}
        anchors = []
        for clause in _clauses(turn):
            for bound, prefix, suffix in (
                ('max', r'不超过|最多|至多|最高|上限(?:是|为)?', r'以内|以下|封顶'),
                ('min', r'不低于|至少|最低|下限(?:是|为)?', r'以上|起步|起'),
            ):
                patterns = [rf'(?:{prefix})\s*(?P<n>{NUMBER})\s*(?:元|块钱|块)?',
                            rf'(?P<n>{NUMBER})\s*(?:元|块钱|块)?\s*(?:{suffix})']
                for pattern in patterns:
                    for match in re.finditer(pattern, clause):
                        number = _number(match['n'])
                        if number and _money_context(clause, match):
                            values[bound].append((number, clause))
            for match in re.finditer(rf'(?P<lo>{NUMBER})\s*元?\s*(?:到|至|[-—~～])\s*(?P<hi>{NUMBER})\s*元?', clause):
                lo, hi = _number(match['lo']), _number(match['hi'])
                if lo and hi and _money_context(clause, match):
                    values['min'].append((lo, clause)); values['max'].append((hi, clause))
            for pattern in (rf'(?:预算|价位|价格)\s*(?:大约|约|是|为)?\s*(?P<n>{NUMBER})\s*(?:元|块钱|块)?',
                            rf'(?P<n>{NUMBER})\s*(?:元|块钱|块)\s*(?:左右|上下|多|来)?',
                            rf'(?P<n>{NUMBER})\s*(?:左右|上下)'):
                for match in re.finditer(pattern, clause):
                    number = _number(match['n'])
                    if number and _money_context(clause, match, allow_vague=True):
                        anchors.append((number, clause))
            # The existing extraction contract defines "四五千" as a midpoint reference,
            # never a hard range. Reject non-money contexts and unit-bearing specs.
            for match in re.finditer(r'(?P<a>[一二两三四五六七八九])(?P<b>[一二两三四五六七八九])千', clause):
                if not re.search(r'存储|内存|屏幕|尺寸|电池|毫安|mAh|英寸|克', clause, re.I):
                    anchors.append(((DIGITS[match['a']] + DIGITS[match['b']]) * 500, clause))
        for key, found in values.items():
            if found:
                claims[key] = _claim(found)
        if anchors and not any(values.values()):
            # A later vague budget revises an old range; it is not a new hard cap.
            claims = {'reference': _claim(anchors)}
        elif any(values.values()):
            if 'reference' in claims:
                del claims['reference']
            if anchors and len({n for n, _ in anchors}) == 1:
                claims['reference'] = _claim(anchors)
    # Conflicting bounds must not sneak back into the draft as inferred anchors.
    if any(v[2] for key, v in claims.items() if key in ('min', 'max')):
        claims.pop('reference', None)
    return claims


def _parameter_claims(text):
    claims = {}
    for turn in _turns(text):
        storage, screen = [], []
        for clause in _clauses(turn):
            if not re.search(r'运行内存|运存|RAM', clause, re.I):
                patterns = [rf'(?:至少|不低于|最低)\s*(?P<n>{NUMBER})\s*(?P<u>GB?|TB)',
                            rf'(?P<n>{NUMBER})\s*(?P<u>GB?|TB)\s*(?:起步|起|以上)',
                            r'(?P<n>64|128|256|512|1024|2048)\s*起步']
                for pattern in patterns:
                    for match in re.finditer(pattern, clause, re.I):
                        number = _number(match['n'])
                        unit = (match.groupdict().get('u') or 'GB').upper()
                        number = number * (1024 if unit == 'TB' else 1) if number else None
                        if number and number >= 32:
                            storage.append((number, clause))
            for match in re.finditer(r'(?P<n>\d+(?:\.\d+)?)\s*(?:英寸|寸)', clause):
                number = float(match['n'])
                if 3 <= number <= 10:
                    if re.search(r'不要|不想要|避开|不考虑|排除|不接受|不能|不选|至少|不超过|小于|大于|以上|以下|\d\s*(?:到|至|[-~～])', clause):
                        claims['screen_review'] = (None, clause, False)
                    else:
                        screen.append((number, clause))
        if storage: claims['min_storage'] = _claim(storage)
        if screen: claims['target_screen'] = _claim(screen)
    return claims


def _brand_key(name):
    return next((key for key, aliases in BRANDS.items() if name.casefold() in [a.casefold() for a in aliases]), name)


def _brand_claims(text):
    states = {}
    for clause in _clauses(text):
        # Mask the game entity while preserving offsets for literal source evidence.
        brand_text = clause.replace('王者荣耀', '    ')
        for key, aliases in BRANDS.items():
            token = '|'.join(re.escape(alias) for alias in aliases)
            match = re.search(token, brand_text, re.I)
            if not match: continue
            before, after = clause[:match.start()], clause[match.end():]
            # Negated negation and permissive acceptance do not imply a whitelist.
            if re.search(r'不是不|并非不|不能不', clause):
                state = 'review'
            elif re.match(r'\s*(?:也|都)?可以|\s*也行', after):
                state = 'neutral'
            elif re.match(r'\s*(?:也|都)?(?:不要|不考虑|排除)', after):
                state = 'exclude'
            else:
                directives = list(re.finditer(r'不想要|不考虑|不要|排除|只(?:看|要|考虑|选|买)|仅(?:看|考虑|选)', before))
                directive = directives[-1][0] if directives else ''
                if directives and re.search(r'不|并非', before[max(0,directives[-1].start()-2):directives[-1].start()]):
                    directive = ''
                state = ('exclude' if directive in ('不想要', '不考虑', '不要', '排除')
                         else 'include' if directive else 'review')
            states[key] = (state, clause)
    return states


def _grade(clause):
    if re.search(r'不是不|并非不|不是最|不必最|不需要最|不要求最|没有不', clause): return None
    if re.search(r'(?:不是|并非|没有|没|不需要|不必|不用|不要求|不算).{0,5}(?:重要|最|必须|一定)|不(?:太|很|那么|怎么)重要', clause):
        return None
    for level, pattern in (
        ('不在意', r'不太?在意|不怎么在意|无所谓|不重要|不看重|不用考虑'),
        ('非常重要', r'非常重要|最重要|最看重|必须|一定要|首要|最轻'),
        ('一般', r'一般|够用|轻一点|轻点|好一点|好点|别太重|不要太重|不用太好|不必太好'),
        ('重要', r'重要|看重|优先|越.+越好|要轻|要好|要强|要大|要更大'),
    ):
        if re.search(pattern, clause): return level
    return None


def _preference_claims(text):
    claims = {}
    for turn in _turns(text):
        found = {}
        for clause in _clauses(turn):
            matches = list(SUBJECT_RE.finditer(clause))
            groups = []
            for match in matches:
                if groups and re.fullmatch(r'\s*(?:和|与|及|、)\s*', clause[groups[-1][-1].end():match.start()]):
                    groups[-1].append(match)
                else: groups.append([match])
            for index, group in enumerate(groups):
                # Include leading modifiers ("必须轻一点") without leaking another subject's negation.
                start = 0 if index == 0 else group[0].start()
                end = groups[index+1][0].start() if index+1 < len(groups) else len(clause)
                fragment = clause[start:end].strip()
                level = _grade(fragment)
                if level:
                    for match in group:
                        found.setdefault(match.lastgroup, []).append((level, fragment))
        claims.update({key: _claim(values) for key, values in found.items()})
    return claims


def verify_extraction(text: str, extraction: dict) -> dict:
    """Return a copied draft with source-grounded suggestions and reviewable anomalies."""
    output = deepcopy(extraction)
    issues = []

    def issue(field, old, new, evidence, kind='rule_mismatch', message=None):
        issues.append({'id': f'{VERSION}:{len(issues)+1}:{field}', 'field': field, 'kind': kind,
                       'model_value': deepcopy(old), 'rule_value': deepcopy(new), 'evidence': evidence,
                       'message': message or '解析结果与原话核验不一致，已填入待确认建议，请检查。'})

    budgets = _budget_claims(text)
    for bound in ('min', 'max', 'reference'):
        old = output['budget'][bound]
        claim = budgets.get(bound)
        expected, evidence, conflict = claim if claim else (None, '', False)
        # A hard range need not duplicate a reference anchor.
        if bound == 'reference' and old is None and any(k in budgets for k in ('min', 'max')):
            continue
        if conflict or old != expected:
            if old is not None or claim:
                issue(f'budget.{bound}', old, expected, evidence,
                      'source_conflict' if conflict else 'rule_mismatch')
                output['budget'][bound] = expected
    low, high = output['budget']['min'], output['budget']['max']
    if low is not None and high is not None and low > high:
        issue('budget', {'min': low, 'max': high}, {'min': None, 'max': None}, text,
              'source_conflict', '预算上下限互相矛盾，已清空，请重新填写预算。')
        output['budget'].update(min=None, max=None, reference=None)

    parameters = _parameter_claims(text)
    for field in ('min_storage', 'target_screen'):
        old = output[field]
        claim = parameters.get(field)
        expected, evidence, conflict = claim if claim else (None, '', False)
        if conflict or old != expected:
            if old is not None or claim:
                issue(field, old, expected, evidence, 'source_conflict' if conflict else 'rule_mismatch')
                output[field] = expected
        if field == 'target_screen' and 'screen_review' in parameters:
            issue(field, old, expected, parameters['screen_review'][1], 'semantic_review',
                  '原话包含尺寸排除或范围要求，无法直接转成一个理想尺寸；请检查下方尺寸和候选资料。')

    brand_states = _brand_claims(text)
    for side in ('include', 'exclude'):
        old = output['brands'][side]
        proposed = [key for key, (state, _) in brand_states.items() if state == side]
        # Unfamiliar positive preference wording is not proof of a wrong whitelist.
        for name in old:
            key = _brand_key(name)
            if key in brand_states and brand_states[key][0] == 'review':
                proposed.append(key)
                issue(f'brands.{side}', old, list(dict.fromkeys(proposed)), brand_states[key][1],
                      'semantic_review', '品牌表达未被规则可靠覆盖，请核对包含或排除是否符合原意。')
        proposed = list(dict.fromkeys(proposed))
        if set(map(_brand_key, old)) != set(proposed):
            issue(f'brands.{side}', old, proposed, '；'.join(dict.fromkeys(v[1] for v in brand_states.values())))
        output['brands'][side] = proposed

    preferences = _preference_claims(text)
    for pref in output['preferences']:
        key, old, evidence = pref['criterion'], pref['level'], pref['evidence']
        claim = preferences.get(key)
        if claim:
            expected, source, conflict = claim
            expected = expected or '未提及'
            if conflict or old != expected or not evidence or evidence not in text:
                issue(f'preferences.{key}', old, expected, source,
                      'source_conflict' if conflict else 'rule_mismatch')
                pref.update(level=expected, evidence=source if not conflict else '',
                            verdict='unknown' if conflict else 'extracted', needs_confirmation=True)
        elif old != '未提及':
            blanket = evidence in ('其他都一般', '其余都一般') and evidence in text
            grounded = bool(evidence and evidence in text and (re.search(SUBJECTS[key], evidence, re.I) or blanket))
            threshold_only = key == 'storage' and 'min_storage' in parameters and _grade(evidence) is None
            if not grounded or threshold_only:
                issue(f'preferences.{key}', old, '未提及', evidence if evidence in text else '', 'unsupported_evidence',
                      '缺少对应维度的可靠偏好依据，已恢复为未提及；门槛不等于偏好，请核对。')
                pref.update(level='未提及', evidence='', verdict='unknown', needs_confirmation=True)
            elif not blanket:
                issue(f'preferences.{key}', old, old, evidence, 'semantic_review',
                      '这段表达未被规则可靠覆盖，保留解析建议，请核对偏好程度。')
                pref.update(verdict='inferred', needs_confirmation=True)

    # Preserve existing budget/brand clarification semantics after corrections.
    output['clarifications'] = [c for c in output['clarifications']
                               if c['field'] != 'budget.max' or (output['budget']['max'] is None and output['budget']['reference'] is None)]
    if output['budget']['max'] is None and output['budget']['reference'] is None and not any(
            c['field'] == 'budget.max' for c in output['clarifications']):
        output['clarifications'].append({'field': 'budget.max', 'question': '请填写预算上限，或明确选择不限。',
                                        'reason': '当前没有可靠的预算上限或参考锚点'})
    output['verification'] = {'version': VERSION, 'status': 'review_required' if issues else 'no_conflicts_detected',
                              'issues': issues}
    if issues: output['status'] = 'degraded'
    return output
