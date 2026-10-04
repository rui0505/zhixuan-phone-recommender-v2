# -*- coding: utf-8 -*-
"""业务规则层：解析、换算、指标定义、字段字典。

规则版本 RULE_VERSION 在 config 中声明；任何解析行为变化都必须同时升版本号，
以便 dataset_releases 能区分「同输入不同结果」的原因。
"""
from __future__ import annotations

import re

RULE_VERSION = "rules-2.0.0"

# ---------------------------------------------------------------- 缺失语义

MISSING_NOT_FOUND = "not_found"        # 已查询但未找到公开实测
MISSING_NOT_REPORTED = "not_reported"  # 来源未公开
MISSING_NOT_APPLICABLE = "not_applicable"

NOT_FOUND_TOKENS = ("未查到公开实测", "未查到公开值", "未查到可靠资料", "未查到可核实",
                    "未查到", "无第三方评测机构评分")
NOT_REPORTED_TOKENS = ("来源未公开", "页面未单列")
EMPTY_TOKENS = ("—", "-", "--", "n/a", "N/A", "")


def classify_missing(raw) -> str | None:
    """把原始文本归类为缺失原因；返回 None 表示不是缺失标记。"""
    if raw is None:
        return MISSING_NOT_FOUND
    s = str(raw).strip()
    if s in EMPTY_TOKENS:
        return MISSING_NOT_FOUND
    for tok in NOT_FOUND_TOKENS:
        if tok in s:
            return MISSING_NOT_FOUND
    for tok in NOT_REPORTED_TOKENS:
        if tok in s:
            return MISSING_NOT_REPORTED
    return None


def parse_number(raw):
    """严格数值解析。

    返回 (value, status)
      status: 'ok' | 'missing' | 'non_numeric'
    只接受纯数字或纯数字带单位后缀的写法；含 '<'、'>'、区间、范围一律 non_numeric，
    不做任何近似或取第一个数字。
    """
    if raw is None:
        return None, "missing"
    if isinstance(raw, bool):
        return None, "non_numeric"
    if isinstance(raw, (int, float)):
        return float(raw), "ok"
    s = str(raw).strip()
    reason = classify_missing(s)
    if reason is not None:
        return None, "missing"
    if s in EMPTY_TOKENS:
        return None, "missing"
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        return float(s), "ok"
    return None, "non_numeric"


# ---------------------------------------------------------------- 内存 / 存储

def parse_ram_gb(raw):
    """'6GB' -> 6.0；无法精确解析返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*GB", s, flags=re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*TB", s, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1024.0
    return None


def parse_storage(raw, tb_to_gb: int = 1024):
    """返回 (storage_gb, memory_type, status)。

    '1TB'            -> (1024, None, 'ok')
    '1TB (LPDDR5T)'  -> (1024, 'LPDDR5T', 'ok')   # 内存类型单列，存储标称不变
    '256GB'          -> (256, None, 'ok')
    """
    if raw is None:
        return None, None, "missing"
    s = str(raw).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(GB|TB)\s*(?:\(([^)]+)\))?", s, flags=re.IGNORECASE)
    if not m:
        return None, None, "unparsed"
    num = float(m.group(1))
    unit = m.group(2).upper()
    mem = (m.group(3) or "").strip() or None
    gb = num if unit == "GB" else num * tb_to_gb
    return gb, mem, "ok"


# ---------------------------------------------------------------- 价格

PRICE_CONDITION_NEW = "new"
PRICE_CONDITION_USED = "used"
PRICE_CONDITION_UNKNOWN = "unknown"

# 价格说明 -> (price_kind, condition)
PRICE_KIND_MAP = {
    "官方在售价": ("official_current_price", PRICE_CONDITION_NEW),
    "官方在售价（2026年9月调价后）": ("official_current_price", PRICE_CONDITION_NEW),
    "官方在售价（2026年7月调价后）": ("official_current_price", PRICE_CONDITION_NEW),
    "官方在售价（焕新版）": ("official_current_price", PRICE_CONDITION_NEW),
    # 淘宝销量第一商品价格：未提供成色/版本证据，按未知处理，不静默归入新机或二手
    "淘宝销量第一商品价格": ("marketplace_listing_price", PRICE_CONDITION_UNKNOWN),
    "二手市场行情价（约）": ("used_market_estimate", PRICE_CONDITION_USED),
}

CURRENCY_SYMBOLS = {"¥": "CNY", "￥": "CNY", "元": "CNY", "$": "USD"}


def parse_price(raw):
    """解析价格原文。

    返回 dict(amount, currency, condition_marker, qualifiers, status)
    含 '（二手）' 时记录 condition_marker='used'；含 '起' 记录 qualifier。
    绝不把区间/起售价取第一个数字当作确定单值。
    """
    res = {"amount": None, "currency": None, "condition_marker": None,
           "qualifiers": [], "status": "missing"}
    if raw is None:
        return res
    if isinstance(raw, (int, float)):
        res.update(amount=float(raw), currency=None, status="ok")
        return res
    s = str(raw).strip()
    if s in EMPTY_TOKENS:
        return res
    if "二手" in s:
        res["condition_marker"] = PRICE_CONDITION_USED
    if "起" in s:
        res["qualifiers"].append("start_from")
    if "约" in s:
        res["qualifiers"].append("approximate")
    for sym, cur in CURRENCY_SYMBOLS.items():
        if sym in s:
            res["currency"] = cur
            break
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        # 只有当整串里只有一个数字时才认为它是确定单值
        nums = re.findall(r"\d+(?:\.\d+)?", s)
        if len(nums) == 1:
            res["amount"] = float(nums[0])
            res["status"] = "ok"
        else:
            res["status"] = "ambiguous_multi_number"
    return res


# ---------------------------------------------------------------- 日期

def parse_release_month(raw):
    """'2023年9月' -> ('2023-09', 'month')；粒度不明返回 (None, 'unknown')。"""
    if raw is None:
        return None, "unknown"
    s = str(raw).strip()
    m = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", "month"
    m = re.fullmatch(r"(\d{4})\s*年", s)
    if m:
        return f"{int(m.group(1)):04d}", "year"
    return None, "unknown"


def extract_source_date(raw):
    """从来源文字中抽取明确出现的年月日/年月；抽取不到返回 (None, 'unknown')。

    只识别原文里字面写出的日期，不推断。
    """
    if raw is None:
        return None, "unknown"
    s = str(raw)
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "day"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", "month"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "day"
    m = re.search(r"(\d{4})\s*年", s)
    if m:
        return m.group(1), "year"
    return None, "unknown"


# ---------------------------------------------------------------- 三元布尔

TRUE_TOKENS = ("有", "支持", "是")
FALSE_TOKENS = ("无", "不支持", "否")
UNKNOWN_TOKENS = ("待核实", "未知", "未查到", "未公开")


def parse_tristate(raw):
    """'有'->True，'无'->False，'待核实…'->None（未知）。"""
    if raw is None:
        return None, "missing"
    s = str(raw).strip()
    if s in EMPTY_TOKENS:
        return None, "missing"
    for tok in UNKNOWN_TOKENS:
        if tok in s:
            return None, "unknown"
    if s in TRUE_TOKENS or s.startswith("有（"):
        return True, "ok"
    if s in FALSE_TOKENS:
        return False, "ok"
    return None, "unparsed"


# ---------------------------------------------------------------- 指标定义

# metric_key -> 定义
METRIC_DEFINITIONS = [
    # key, 中文名, 单位, 方向, 可比性分组前缀, 值类型, 范围检查
    ("dxomark_overall", "DXOMARK总分", "分", "benefit", "dxomark", "float", "0-200"),
    ("dxomark_photo", "DXOMARK拍照分", "分", "benefit", "dxomark", "float", "0-200"),
    ("dxomark_video", "DXOMARK视频分", "分", "benefit", "dxomark", "float", "0-200"),
    ("dxomark_zoom", "DXOMARK变焦分", "分", "benefit", "dxomark", "float", "0-200"),
    ("dxomark_lowlight", "DXOMARK夜景分", "分", "benefit", "dxomark", "float", "0-200"),
    ("dxomark_normalized_100", "DXOMARK归一化分值", "分", "benefit",
     "source_normalized", "float", "0-100"),
    ("phonearena_camera_overall", "PhoneArena相机总分", "分", "benefit",
     "phonearena", "float", "0-200"),
    ("phonearena_camera_photo", "PhoneArena相机照片分", "分", "benefit",
     "phonearena", "float", "0-200"),
    ("honor_of_kings_avg_fps", "王者荣耀平均帧率", "FPS", "benefit", "game_hok", "float", "0-200"),
    ("honor_of_kings_power_w", "王者荣耀平均功耗", "W", "cost", "game_hok", "float", "0-30"),
    ("honor_of_kings_1pct_low_fps", "王者荣耀1%Low帧", "FPS", "benefit", "game_hok", "float", "0-200"),
    ("honor_of_kings_max_temp_c", "王者荣耀机身最高温度", "℃", "cost", "game_hok", "float", "20-60"),
    ("display_peak_brightness_nit", "屏幕亮度", "nit", "benefit", "display_brightness", "float", "100-10000"),
    ("battery_capacity_mah", "电池典型容量", "mAh", "benefit", "battery", "float", "500-20000"),
    ("weight_g", "整机重量", "g", "cost", "weight", "float", "80-400"),
    ("screen_size_inch", "屏幕尺寸", "英寸", "target", "screen_size", "float", "3-9"),
    ("refresh_rate_hz", "屏幕刷新率", "Hz", "benefit", "refresh_rate", "float", "30-200"),
    ("wired_charge_w", "有线充电功率", "W", "benefit", "charging", "float", "1-400"),
    ("wireless_charge_w", "无线充电最高功率", "W", "benefit", "charging", "float", "0-100"),
]

# 不作为跨协议统一排名指标，仅作源数据导入
NON_COMPARABLE_METRICS = {"dxomark_normalized_100"}


def comparison_group(metric_key: str, protocol: str | None, condition: str | None = None) -> str:
    """可比性分组：来源 + 协议 + 条件组合。"""
    base = dict((m[0], m[4]) for m in METRIC_DEFINITIONS).get(metric_key, "other")
    if metric_key.startswith("dxomark_"):
        return f"{base}_{protocol or 'unknown'}"
    if metric_key.startswith("phonearena_"):
        return base
    if metric_key.startswith("honor_of_kings_"):
        return f"{base}_{condition or 'unknown'}"
    if metric_key == "display_peak_brightness_nit":
        return f"{base}_{condition or 'unknown'}"
    return base


# ---------------------------------------------------------------- 规格层可比性分组
#
# 规格层的「可比性分组」不能一律拿 note 当键：note 在不同 spec_key 下语义不同——
#   - display_peak_brightness_nit：note = 源表真实「亮度口径」列（协议语义）
#   - wired_charge_w / wireless_charge_w / refresh_rate_hz：note = 单元格原文（仅解析来源）
# 用原文当分组键会把同一物理量按措辞拆碎（充电功率 38 组、刷新率 7 组），
# 结果每台机型只与自己可比。因此：
#   - 只有真正带口径语义的规格按「规范化口径类别」分组；
#   - 其余规格同一 spec_key 内数值同量纲，直接可比，键为 spec_key 本身。
# 原文一律保留在 specifications.note / raw_value，不丢弃、不改写。

SPEC_PROTOCOL_KEYS = {"display_peak_brightness_nit"}

# 顺序敏感：越具体的口径越靠前（「官方最大亮度（典型值）」应判为 typical 而非峰值）
# unverified 必须最先判：它是数据质量状态（口径未核实），不是口径描述，
# 与「官方峰值（窗口未注明）」这类「来源未说明范围」不是同一回事，不可混为一类。
BRIGHTNESS_PROTOCOL_CATEGORIES = (
    ("unverified", ("未核实", "未确认", "待核实", "不明", "未知", "未注明来源")),
    ("typical", ("典型",)),
    ("local_peak", ("局部",)),
    ("hbm", ("HBM", "高亮度模式")),
    ("global", ("全局", "全屏")),
    ("unspecified_peak", ("峰值", "最高亮度", "最大亮度")),
)


def brightness_protocol_category(note: str) -> str:
    """把亮度口径原文归到保守类别；无法归类记 other，绝不并入已知类别。"""
    s = (note or "").strip()
    if not s:
        return "unspecified"
    for name, keywords in BRIGHTNESS_PROTOCOL_CATEGORIES:
        if any(k in s for k in keywords):
            return name
    return "other"


def spec_comparison_group(spec_key: str, note: str = "") -> str:
    """规格层可比性分组键。原文仍存 note，本键只用于分组比较。"""
    if spec_key in SPEC_PROTOCOL_KEYS:
        return f"{spec_key}::{brightness_protocol_category(note)}"
    return f"spec::{spec_key}"


# ---------------------------------------------------------------- 评测摘录与检索标签

# 检索标签规则（keyword_rule_v1）：只做关键词命中，不做语义判断。
# 标签属于自动提取结果，必须与源表里的人工文字分开标记
# （evidence_snippets.origin / tag_method）。
TAG_RULES = [
    ("夜景", ("夜景", "低光", "暗光", "夜拍", "lowlight", "Lowlight")),
    ("视频", ("视频", "录像", "Video")),
    ("长焦变焦", ("长焦", "变焦", "潜望", "远摄", "Tele", "Zoom")),
    ("超广角", ("超广角", "ultra-wide", "Ultra Wide")),
    ("人像", ("人像", "portrait", "Portrait")),
    ("发热温度", ("温度", "发热", "降温", "℃")),
    ("帧率游戏", ("帧率", "王者", "FPS", "120帧", "60帧", "游戏")),
    ("续航充电", ("续航", "电池", "充电", "mAh")),
    ("屏幕亮度", ("亮度", "HBM", "nit", "峰值亮度", "激发亮度")),
    ("拍照影像", ("拍照", "影像", "相机", "主摄", "成像", "摄影")),
    ("折叠屏", ("折叠", "内屏", "外屏", "铰链")),
    ("卫星通信", ("卫星",)),
    ("重量手感", ("重量", "轻薄", "手感")),
    ("芯片性能", ("芯片", "处理器", "骁龙", "天玑", "跑分", "性能模式")),
]
TAG_RULE_VERSION = "keyword_rule_v1"

# 摘录类型 -> 该摘录可支持的指标键
SNIPPET_KIND_METRICS = {
    "review_conclusion": ["dxomark_overall", "dxomark_photo", "dxomark_video"],
    "lowlight_note": ["dxomark_lowlight"],
    "zoom_note": ["dxomark_zoom"],
    "other_agency_score": ["phonearena_camera_overall", "phonearena_camera_photo"],
    "hardware_note": [],
    "camera_note": [],
    "game_data_note": ["honor_of_kings_avg_fps", "honor_of_kings_power_w",
                       "honor_of_kings_1pct_low_fps", "honor_of_kings_max_temp_c"],
    "config_note": [],
    "battery_protocol": [],
    "brightness_protocol": [],
}


def extract_tags(text) -> list:
    """按关键词规则生成检索标签；返回命中的标签列表（可为空）。"""
    if not text:
        return []
    s = str(text)
    return [tag for tag, kws in TAG_RULES if any(k in s for k in kws)]


# ---------------------------------------------------------------- 字段字典

# (字段, 层级, 单位, 说明)
FIELD_DICTIONARY = [
    ("model_id", "规范", "-", "机型稳定 ID，M+4 位序号，由 id_registry.csv 持久分配"),
    ("variant_id", "规范", "-", "硬件版本稳定 ID，V+4 位序号；地区/卫星通信等差异"),
    ("sku_id", "规范", "-", "购买配置稳定 ID，S+4 位序号；RAM/存储/内存类型组合"),
    ("source_record_id", "原始", "-", "源记录 ID，绑定输入哈希+工作表+源行号，仅用于追溯"),
    ("产品名称", "原始只读原文", "-", "源表产品名称原样，不做全半角或空白归一"),
    ("运行内存", "原始只读原文", "-", "源表运行内存原文，如 6GB"),
    ("存储", "原始只读原文", "-", "源表存储原文，如 1TB (LPDDR5T)"),
    ("ram_gb", "派生", "GB", "由 运行内存 按 ram-1.0 规则换算；无法精确解析为 NULL"),
    ("storage_gb", "派生", "GB", "由 存储 换算；1TB=1024 为项目换算约定，原始标称不变"),
    ("memory_type", "派生", "-", "内存类型；仅当原文括号标注时取值，否则 NULL"),
    ("price_raw", "原始只读原文", "-", "源表价格原文，含 ¥/元/（二手）等符号"),
    ("price_cny", "派生", "元", "价格数值；多数字或区间一律 NULL 并登记问题"),
    ("price_kind", "派生", "-", "official_current_price/marketplace_listing_price/used_market_estimate"),
    ("condition", "派生", "-", "new/used/unknown；无成色证据时不静默归类"),
    ("is_estimated", "派生", "-", "1 表示原文含「约」，为估计值"),
    ("source_sale_status", "原始只读原文", "-", "源表销售状态原文（在售/停售），不直接等于渠道无库存"),
    ("release_month_raw", "原始只读原文", "-", "源表发布时间原文，如 2023年9月"),
    ("release_month", "派生", "YYYY-MM", "发布日期；仅到月份粒度，不补造日"),
    ("raw_value", "原始", "-", "原始单元格值文本表示，数值用 repr 保真"),
    ("cached_value", "原始", "-", "公式缓存值；源表无公式时为空"),
    ("formula", "原始", "-", "公式表达式；源表无公式时为空"),
    ("number_format", "原始", "-", "源表数字格式字符串"),
    ("missing_reason", "派生", "-", "not_found / not_reported / not_applicable；NULL 表示有实测值"),
    ("snippet_text", "原始只读原文", "-", "评测结论/硬件补充/备注原文摘录，逐字保留，不改写"),
    ("source_col", "原始", "-", "产生摘录原文的源表列名（如「续航测试口径」）"),
    ("source_ref_col", "原始", "-", "驱动 source_id 的源表来源列名（如「续航来源」）；仅当该列为空时 source_id 才为 NULL"),
    ("origin", "派生", "-", "source_table_human（源表人工撰写）/ system_label（系统生成标签）"),
    ("extraction_method", "派生", "-", "source_column（源表列直取）/ keyword_rule_v1（关键词标签）"),
    ("tags", "派生", "-", "检索标签，逗号分隔；由 keyword_rule_v1 自动提取，非人工结论"),
    ("comparison_group", "派生", "-",
     "规格层可比性分组键：仅亮度按规范化口径类别分组（原文仍存 note），"
     "其余规格同一 spec_key 内数值同量纲直接可比；不同组不直接混排"),
]
