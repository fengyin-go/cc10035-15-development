"""正文证据规则：类别证据信号、门槛口径、字段模板与示例正文。

这里是纯规则层，不读写数据仓库，供内容分类、模板规则和正文证据流水线共用。
口径（profile）一旦发布就不再原地修改，需要调整门槛时新增一个 profile，
这样历史数据集和试跑样本始终能按当时的正文版本与门槛口径解释。
"""
from __future__ import annotations

import re
from typing import Any, Callable

DEFAULT_PROFILE = "gate-2026.11-v1"

# 类别优先级只在证据得分与字段覆盖率都相同时用于稳定裁定顺序。
CATEGORY_PRIORITY = {"新闻媒体": 0, "电子商务": 1, "技术资讯": 2}

CATEGORY_FAMILY = {"新闻媒体": "news", "电子商务": "product", "技术资讯": "tech"}

# 二级分类提示：只用于在已确定一级分类后细分，必须与内容分类的层级表保持一致。
SECONDARY_HINTS: dict[str, list[tuple[str, list[str]]]] = {
    "新闻媒体": [
        ("时政新闻", ["政策", "政府", "委员会", "通报", "规划"]),
        ("财经新闻", ["经济", "金融", "股市", "营收", "资金"]),
        ("科技新闻", ["芯片", "人工智能", "技术", "新品", "发布"]),
        ("体育新闻", ["比赛", "球队", "赛事", "联赛"]),
    ],
    "电子商务": [
        ("B2C平台", ["旗舰店", "购物车", "包邮", "促销"]),
        ("B2B平台", ["批发", "采购", "询价", "起订量"]),
        ("跨境零售", ["跨境", "海外", "清关", "直邮"]),
        ("社交零售", ["直播", "社群", "拼团"]),
    ],
    "技术资讯": [
        ("编程语言", ["Python", "Java", "Go", "Rust", "TypeScript"]),
        ("架构设计", ["架构", "微服务", "分布式"]),
        ("AI/ML", ["模型", "人工智能", "训练", "推理"]),
        ("DevOps", ["Docker", "Kubernetes", "部署", "CI/CD"]),
    ],
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "新闻媒体": {
        "template_name": "新闻资讯模板",
        "family": "news",
        "min_coverage": 0.75,
        "selectors": {
            "标题": "h1.article-title",
            "发布时间": "time.publish-at",
            "作者": ".article-author",
            "正文首段": "article p:first-of-type",
        },
        "fields": [
            {"field": "标题", "key": "title", "patterns": [r"(?m)^标题[:：]\s*([^\n]+)", r"\A([^\n]+)"]},
            {
                "field": "发布时间",
                "key": "published_at",
                "patterns": [
                    r"(?m)^(?:发布时间|时间)[:：]\s*([^\n]+)",
                    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?",
                ],
            },
            {
                "field": "作者",
                "key": "author",
                "patterns": [r"(?m)^作者[:：]\s*([^\n]+)", r"记者\s*([^\s，,。；;]{2,12})"],
            },
            {
                "field": "正文首段",
                "key": "summary",
                "patterns": [
                    r"(?m)^(?!标题[:：]|发布时间[:：]|作者[:：]|来源[:：])[^\n]{40,}$",
                ],
            },
        ],
    },
    "电子商务": {
        "template_name": "商品详情模板",
        "family": "product",
        "min_coverage": 0.75,
        "selectors": {
            "商品名称": "h1.product-name",
            "价格": ".price-box .price",
            "库存状态": ".stock-status",
            "店铺": ".shop-name",
        },
        "fields": [
            {
                "field": "商品名称",
                "key": "product_name",
                "patterns": [r"(?m)^商品名称[:：]\s*([^\n]+)", r"(?m)^(?:商品标题|品名)[:：]\s*([^\n]+)"],
            },
            {
                "field": "价格",
                "key": "price",
                "patterns": [r"(?m)^(?:价格|售价|促销价)[:：]\s*([^\n]+)", r"[¥￥]\s?\d[\d,]*(?:\.\d+)?"],
            },
            {
                "field": "库存状态",
                "key": "stock",
                "patterns": [r"(?m)^(?:库存状态|库存)[:：]\s*([^\n]+)", r"(现货|缺货|有货|预售)"],
            },
            {
                "field": "店铺",
                "key": "shop",
                "patterns": [r"(?m)^(?:店铺|商家|卖家)[:：]\s*([^\n]+)", r"([^\s，,。]*旗舰店)"],
            },
        ],
    },
    "技术资讯": {
        "template_name": "技术文章模板",
        "family": "tech",
        "min_coverage": 0.75,
        "selectors": {
            "文章标题": "h1.post-title",
            "代码语言": "pre code[data-lang]",
            "技术主题": ".post-tags",
        },
        "fields": [
            {"field": "文章标题", "key": "title", "patterns": [r"(?m)^标题[:：]\s*([^\n]+)", r"\A([^\n]+)"]},
            {
                "field": "代码语言",
                "key": "code_lang",
                "patterns": [r"(?:代码语言|语言)[:：]\s*([^\n]+)", r"\b(Python|Java|Go|Rust|TypeScript|SQL)\b"],
            },
            {
                "field": "技术主题",
                "key": "topic",
                "patterns": [r"(?:技术主题|主题)[:：]\s*([^\n]+)", r"(API|SDK|架构|框架|算法|性能优化|微服务)"],
            },
        ],
    },
}

# 每条信号权重合计为 1.0，得分即命中信号权重之和。
_SIGNAL_DEFS: dict[str, list[dict[str, Any]]] = {
    "新闻媒体": [
        {"id": "news.headline", "label": "标题/导语", "weight": 0.20, "pattern": r"\A[^\n]{4,60}"},
        {"id": "news.dateline", "label": "电头/记者署名", "weight": 0.25, "pattern": r"本报讯|记者|来源[:：]|通讯社|报道"},
        {
            "id": "news.publish_time",
            "label": "发布时间",
            "weight": 0.15,
            "pattern": r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?",
        },
        {
            "id": "news.topic",
            "label": "新闻事件用语",
            "weight": 0.20,
            "pattern": r"发布会|政策|采访|宣布|启动|规划|政府|委员会|通报",
        },
        {"id": "news.article_body", "label": "成段正文", "weight": 0.20, "pattern": r"(?m)^[^\n]{80,}$"},
    ],
    "电子商务": [
        {
            "id": "product.sku",
            "label": "商品名称/型号",
            "weight": 0.20,
            "pattern": r"(?:商品名称|商品标题|品名|型号|SKU)[:：]\s*[^\n]+",
        },
        {
            "id": "product.price",
            "label": "价格信息",
            "weight": 0.30,
            "pattern": r"(?:价格|售价|促销价)[:：]\s*[^\n]+|[¥￥$]\s?\d[\d,]*(?:\.\d+)?",
        },
        {
            "id": "product.stock",
            "label": "库存状态",
            "weight": 0.20,
            "pattern": r"(?:库存状态|库存)[:：]\s*[^\n]+|(?:现货|缺货|有货|预售)",
        },
        {
            "id": "product.shop",
            "label": "店铺/商家",
            "weight": 0.15,
            "pattern": r"(?:店铺|商家|卖家|旗舰店)[:：]?\s*[^\n]+|官方旗舰店",
        },
        {
            "id": "product.cart",
            "label": "购买/促销入口",
            "weight": 0.15,
            "pattern": r"加入购物车|立即购买|下单|优惠券|促销|包邮",
        },
    ],
    "技术资讯": [
        {"id": "tech.code", "label": "代码块", "weight": 0.30, "pattern": r"代码块|```|\bdef \b|\bclass \b|\bfunction \b|SELECT "},
        {
            "id": "tech.topic",
            "label": "技术主题",
            "weight": 0.25,
            "pattern": r"API|SDK|架构|框架|算法|模型|性能优化|微服务",
        },
        {"id": "tech.version", "label": "版本号", "weight": 0.20, "pattern": r"v\d+\.\d+|\b\d+\.\d+\.\d+\b|版本[:：]"},
        {
            "id": "tech.stack",
            "label": "技术栈",
            "weight": 0.25,
            "pattern": r"Python|Java|Go|Rust|TypeScript|Vue|React|Kubernetes|Docker",
        },
    ],
}

_COMPILED: dict[str, list[dict[str, Any]]] = {
    category: [
        {**signal, "regex": re.compile(str(signal["pattern"]), re.M)}
        for signal in signals
    ]
    for category, signals in _SIGNAL_DEFS.items()
}

# 门槛口径：threshold 为证据得分下限，min_signals 为至少命中的信号条数。
# v2 调高了新闻门槛，用于验证“规则调整后重算”不会把历史样本的依据冲掉。
PROFILES: dict[str, dict[str, Any]] = {
    "gate-2026.11-v1": {
        "profile_id": "gate-2026.11-v1",
        "label": "正文证据门槛 v1",
        "min_body_chars": 40,
        "categories": {
            "新闻媒体": {"threshold": 0.55, "min_signals": 2},
            "电子商务": {"threshold": 0.55, "min_signals": 2},
            "技术资讯": {"threshold": 0.60, "min_signals": 2},
        },
    },
    "gate-2026.11-v2": {
        "profile_id": "gate-2026.11-v2",
        "label": "正文证据门槛 v2（收紧新闻）",
        "min_body_chars": 40,
        "categories": {
            "新闻媒体": {"threshold": 0.75, "min_signals": 3},
            "电子商务": {"threshold": 0.55, "min_signals": 2},
            "技术资讯": {"threshold": 0.60, "min_signals": 2},
        },
    },
}

DEMO_PAGE_URI = "https://demo.example.com/feed/item-1001"

DEMO_BODIES: list[dict[str, str]] = [
    {
        "key": "news",
        "title": "新闻正文",
        "expected_categories": "新闻媒体",
        "body": (
            "标题：城市更新三年行动启动\n"
            "发布时间：2026-11-18 09:30\n"
            "作者：记者 李明\n"
            "本报讯（记者 李明）市住建委今日举行新闻发布会，宣布城市更新三年行动正式启动。"
            "首批项目涉及十二个社区，重点改善老旧小区管网和公共空间配套。\n"
            "据项目负责人介绍，本轮更新将分三年推进，今年先完成三个示范社区的改造，"
            "并在年底前公布评估结果。发布会还通报了资金安排和居民意见征集渠道。"
        ),
    },
    {
        "key": "product",
        "title": "商品正文",
        "expected_categories": "电子商务",
        "body": (
            "商品名称：星岚 X1 无线降噪耳机\n"
            "价格：￥399.00\n"
            "库存状态：现货\n"
            "店铺：星岚官方旗舰店\n"
            "商品卖点：40小时续航，支持主动降噪和双设备连接。"
            "加入购物车即可享受包邮服务，促销期间赠送收纳盒。"
        ),
    },
    {
        "key": "borderline",
        "title": "新闻边缘正文（v1 通过、v2 收紧后低于门槛）",
        "expected_categories": "新闻媒体",
        "body": (
            "标题：社区图书馆延长开放时间\n"
            "发布时间：2026-11-15 08:00\n"
            "为方便读者晚间借阅，社区图书馆自本周起将闭馆时间调整至二十一点，"
            "并在周末增加亲子阅读专场，读者可以通过线上系统预约座位和借阅名额，"
            "馆方也会根据预约情况补充热门书目的复本数量。\n"
            "馆内自助借还设备同步升级，读者凭读者证即可完成借阅和续借操作。"
        ),
    },
    {
        "key": "empty",
        "title": "正文缺失（不得进入可采集模板集合）",
        "expected_categories": "未分类",
        "body": "",
    },
    {
        "key": "mixed",
        "title": "混合正文（同时含新闻与商品证据）",
        "expected_categories": "新闻媒体、电子商务",
        "body": (
            "标题：星岚 X1 新品发布与城市体验店开业\n"
            "发布时间：2026-11-20 10:15\n"
            "作者：记者 王蕾\n"
            "本报讯（记者 王蕾）星岚科技今日举行发布会，宣布 X1 无线降噪耳机正式上市，"
            "并在城市更新示范街区开设首家体验店。首批体验店将覆盖三个社区，"
            "后续还会公布体验店的服务规划和居民预约方式。\n"
            "商品名称：星岚 X1 无线降噪耳机\n"
            "价格：￥399.00\n"
            "库存状态：现货\n"
            "店铺：星岚官方旗舰店\n"
            "消费者可在门店试听后加入购物车，享受包邮和促销赠品。"
            "页面由内容 API 生成，项目将分阶段公布体验店规划。"
        ),
    },
]


def list_profiles() -> list[dict[str, Any]]:
    """返回可用门槛口径（含各类别门槛），供前端和接口展示。"""
    result = []
    for profile in PROFILES.values():
        result.append({
            "profile_id": profile["profile_id"],
            "label": profile["label"],
            "min_body_chars": profile["min_body_chars"],
            "categories": [
                {
                    "category": category,
                    "template_name": TEMPLATES[category]["template_name"],
                    "threshold": rule["threshold"],
                    "min_signals": rule["min_signals"],
                    "signals": [
                        {"id": signal["id"], "label": signal["label"], "weight": signal["weight"]}
                        for signal in _SIGNAL_DEFS[category]
                    ],
                }
                for category, rule in profile["categories"].items()
            ],
        })
    return result


def get_profile(profile_id: str | None) -> dict[str, Any]:
    """取门槛口径；未指定时使用默认口径，未知口径直接报错。"""
    key = profile_id or DEFAULT_PROFILE
    if key not in PROFILES:
        raise KeyError(f"未知门槛口径：{key}")
    return PROFILES[key]


def normalize_body(body: str) -> str:
    """统一换行并去掉首尾空白，正文版本按归一化后的内容计算。"""
    return (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _quote_window(content: str, start: int, end: int, before: int = 18, after: int = 60) -> dict[str, Any]:
    quote_start = max(0, start - before)
    quote_end = min(len(content), max(end, start) + after)
    return {
        "quote_start": quote_start,
        "quote_end": quote_end,
        "quote": content[quote_start:quote_end],
        "match_text": content[start:end],
    }


def evaluate_body(body: str, profile_id: str | None = None) -> dict[str, Any]:
    """按门槛口径校验正文并计算每个类别的证据得分。

    缺正文、正文过短时返回 body_valid=False 且 candidates 为空，
    下游无法据此生成任何模板候选或试跑样本。
    """
    profile = get_profile(profile_id)
    content = normalize_body(body)
    if not content:
        return {
            "profile_id": profile["profile_id"],
            "body_valid": False,
            "body_status": "正文缺失",
            "body_reason": "未采集到页面正文，无法校验任何正文证据",
            "char_count": 0,
            "candidates": [],
        }
    if len(content) < profile["min_body_chars"]:
        return {
            "profile_id": profile["profile_id"],
            "body_valid": False,
            "body_status": "正文不足",
            "body_reason": (
                f"正文长度 {len(content)} 字符，低于门槛口径要求的最小长度 "
                f"{profile['min_body_chars']} 字符"
            ),
            "char_count": len(content),
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for category, rule in profile["categories"].items():
        matched: list[dict[str, Any]] = []
        for signal in _COMPILED[category]:
            match = signal["regex"].search(content)
            if match is None:
                continue
            window = _quote_window(content, match.start(), match.end())
            matched.append({
                "signal_id": signal["id"],
                "signal_label": signal["label"],
                "weight": signal["weight"],
                "start": match.start(),
                "end": match.end(),
                **window,
            })
        score = round(sum(item["weight"] for item in matched), 2)
        qualified = score + 1e-9 >= rule["threshold"] and len(matched) >= rule["min_signals"]
        if qualified:
            gate = "通过"
            gate_reason = (
                f"证据得分 {score:.2f} ≥ 门槛 {rule['threshold']:.2f}，"
                f"命中 {len(matched)} 条信号（至少 {rule['min_signals']} 条）"
            )
        else:
            gate = "低于门槛"
            if len(matched) < rule["min_signals"]:
                gate_reason = (
                    f"命中 {len(matched)} 条信号，少于要求的 {rule['min_signals']} 条；"
                    f"得分 {score:.2f}，门槛 {rule['threshold']:.2f}"
                )
            else:
                gate_reason = f"证据得分 {score:.2f} 低于门槛 {rule['threshold']:.2f}"
        template = TEMPLATES[category]
        candidates.append({
            "category": category,
            "template_name": template["template_name"],
            "template_family": template["family"],
            "evidence_score": score,
            "threshold": rule["threshold"],
            "min_signals": rule["min_signals"],
            "matched_signal_count": len(matched),
            "matched_signals": matched,
            "gate": gate,
            "gate_reason": gate_reason,
            "qualified": qualified,
        })
    return {
        "profile_id": profile["profile_id"],
        "body_valid": True,
        "body_status": "正文有效",
        "body_reason": f"正文长度 {len(content)} 字符，满足最小长度 {profile['min_body_chars']} 字符",
        "char_count": len(content),
        "candidates": candidates,
    }


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按证据得分降序、类别优先级升序排列，确保裁定结果可复现。"""
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("evidence_score", 0)),
            CATEGORY_PRIORITY.get(str(item.get("category", "")), 99),
        ),
    )


def classify(evaluation: dict[str, Any], body: str = "") -> dict[str, Any]:
    """从正文证据得出分类标签；没有正文时明确标记为未分类。"""
    candidates = evaluation.get("candidates") or []
    if not evaluation.get("body_valid"):
        return {
            "primary_category": "未分类",
            "secondary_category": "—",
            "confidence": 0.0,
            "qualified_categories": [],
            "basis": evaluation.get("body_reason", "缺少正文，不能按正文证据分类"),
            "body_status": evaluation.get("body_status", "正文缺失"),
        }
    ranked = rank_candidates(candidates)
    qualified = [item for item in ranked if item.get("qualified")]
    pool = qualified or [item for item in ranked if float(item.get("evidence_score", 0)) > 0]
    if not pool:
        best = None
        primary = "未分类"
        confidence = 0.0
        basis = "正文有效，但未命中任何类别证据信号"
    else:
        best = pool[0]
        primary = str(best["category"])
        confidence = float(best["evidence_score"])
        if qualified:
            basis = (
                f"正文证据命中 {len(qualified)} 个类别："
                + "、".join(str(item["category"]) for item in qualified)
                + f"；主类别「{primary}」证据得分 {confidence:.2f}"
            )
        else:
            basis = (
                f"无类别达到模板证据门槛；最高分「{primary}」{confidence:.2f}，"
                "仅作分类参考，不进入模板试跑"
            )
    others = [str(item["category"]) for item in ranked
              if best is not None and item["category"] != best["category"]
              and float(item.get("evidence_score", 0)) > 0]
    secondary = "—"
    hints = SECONDARY_HINTS.get(primary)
    if hints:
        content = normalize_body(body)
        secondary = next(
            (name for name, keywords in hints if any(word in content for word in keywords)),
            hints[0][0],
        )
    return {
        "primary_category": primary,
        "secondary_category": secondary if hints else ("、".join(others) if others else "—"),
        "confidence": confidence,
        "qualified_categories": [str(item["category"]) for item in qualified],
        "basis": basis,
        "body_status": evaluation.get("body_status", "正文有效"),
    }


def _match_value(content: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    for pattern in spec["patterns"]:
        match = re.search(pattern, content, re.M)
        if match is None:
            continue
        value = match.group(1) if match.groups() else match.group(0)
        value = (value or "").strip()
        if not value:
            continue
        start, end = match.span(1) if match.groups() else match.span(0)
        window = _quote_window(content, start, end, before=20, after=40)
        return {
            "value": value,
            "start": start,
            "end": end,
            **window,
        }
    return None


def extract_fields(body: str, category: str) -> list[dict[str, Any]]:
    """按模板字段规则从正文抽取试跑值，并保留正文出处（偏移与片段）。"""
    content = normalize_body(body)
    template = TEMPLATES.get(category)
    if not template:
        return []
    results: list[dict[str, Any]] = []
    for spec in template["fields"]:
        hit = _match_value(content, spec)
        if hit is None:
            results.append({
                "field": spec["field"],
                "key": spec["key"],
                "value": None,
                "status": "未匹配",
                "start": None,
                "end": None,
                "quote": None,
                "match_text": None,
            })
            continue
        results.append({
            "field": spec["field"],
            "key": spec["key"],
            "value": hit["value"],
            "status": "命中",
            "start": hit["start"],
            "end": hit["end"],
            "quote_start": hit["quote_start"],
            "quote_end": hit["quote_end"],
            "quote": hit["quote"],
            "match_text": hit["match_text"],
        })
    return results


def demo_payload() -> dict[str, Any]:
    """给前端的演示入口：同一 URI 的新闻、商品、混合三份正文。"""
    return {
        "page_uri": DEMO_PAGE_URI,
        "profile_id": DEFAULT_PROFILE,
        "profiles": list_profiles(),
        "bodies": DEMO_BODIES,
    }
