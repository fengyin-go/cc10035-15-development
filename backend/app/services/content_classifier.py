"""内容分类领域服务：对采集页面进行多级内容标签分类。"""
from __future__ import annotations

import hashlib
from typing import Any

from app.services import evidence_rules
from app.services.base import BaseModuleService
from app.store import store


CATEGORY_HIERARCHY = {
    "电子商务": ["B2C平台", "B2B平台", "跨境零售", "社交零售"],
    "新闻媒体": ["时政新闻", "财经新闻", "科技新闻", "体育新闻"],
    "企业官网": ["产品展示", "关于我们", "解决方案", "客户案例"],
    "技术资讯": ["编程语言", "架构设计", "AI/ML", "DevOps"],
    "政府网站": ["政策法规", "政务公开", "公共服务", "数据统计"],
    "教育学术": ["课程资源", "学术论文", "研究报告", "在线教育"],
}


class ContentClassifierService(BaseModuleService):
    MODULE = "content_classifier"
    ENTITY = "分类标签"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "标签ID", "页面URI", "一级分类", "二级分类", "置信度", "分类依据", "标注时间",
        "正文版本", "正文哈希", "正文状态", "门槛口径", "模板候选ID", "采纳模板",
        "试跑结果", "试跑回执ID",
    ]
    REQUIRED_FIELDS = ["标签ID", "页面URI", "一级分类"]
    STATUS_ORDER = ["待分类", "已分类", "已复核", "已订正"]
    ACTION_RULES = {"提交分类": "待分类", "复核标签": "已分类", "订正分类": "已分类"}
    NEGATIVE_ACTIONS = []

    def classify_page(
        self, page_uri: str, page_content: str = "", profile_id: str | None = None
    ) -> dict[str, Any]:
        """对单个页面进行内容分类：有正文时按正文证据标注，无正文时明确未分类。

        页面 URI 只用于生成标签标识；分类本身不再由 URI 哈希决定。
        """
        try:
            profile = evidence_rules.get_profile(profile_id)
        except KeyError as exc:
            return {"ok": False, "reason": str(exc)}
        evaluation = evidence_rules.evaluate_body(page_content, profile["profile_id"])
        classification = evidence_rules.classify(evaluation, page_content)
        tag_id = hashlib.sha256(page_uri.encode()).hexdigest()[:12]
        return {
            "ok": True,
            "标签ID": tag_id,
            "页面URI": page_uri,
            "一级分类": classification["primary_category"],
            "二级分类": classification["secondary_category"],
            "置信度": f"{classification['confidence']:.2f}",
            "分类依据": classification["basis"],
            "标注时间": "2026-11-19T10:00:00Z",
            "正文状态": classification["body_status"],
            "门槛口径": profile["profile_id"],
            "达标类别": classification["qualified_categories"],
            "候选详情": [
                {
                    "category": item["category"],
                    "evidence_score": item["evidence_score"],
                    "threshold": item["threshold"],
                    "gate": item["gate"],
                    "gate_reason": item["gate_reason"],
                }
                for item in evaluation["candidates"]
            ],
        }

    def build_template_candidates(
        self, page_uri: str, page_content: str, *, profile_id: str | None = None
    ) -> dict[str, Any]:
        """消费正文证据并生成模板候选依据数据集，交模板规则模块使用。"""
        from app.services.template_rule import TemplateRuleService

        return TemplateRuleService().build_candidate_dataset(
            page_uri, page_content, source="content_classifier", profile_id=profile_id)

    def feed_extract_metric(self) -> dict[str, Any]:
        """将分类结果提供给抽取指标进行效率分析。"""
        rows = store.rows(self.MODULE)
        category_dist: dict[str, int] = {}
        for row in rows:
            cat = row.get("一级分类", "未分类")
            category_dist[cat] = category_dist.get(cat, 0) + 1
        return {
            "total_classified": len(rows),
            "category_distribution": category_dist,
            "unique_categories": len(category_dist),
        }

    def feed_template_rules(self) -> dict[str, Any]:
        """按分类审核清单里的采纳结果推荐模板，不再按 URI 标签硬匹配站点。

        只有试跑已采纳（正文通过证据门槛）的记录才会带出模板，
        缺正文或低于门槛的记录会出现在 blocked 里供复核。
        """
        rows = store.rows(self.MODULE)
        recommendations: dict[str, list[dict[str, Any]]] = {}
        blocked: list[dict[str, Any]] = []
        for row in rows:
            category = row.get("一级分类", "未分类")
            if row.get("采纳模板") in (None, "", "—"):
                blocked.append({
                    "page_uri": row.get("页面URI"),
                    "category": category,
                    "reason": row.get("试跑结果") or row.get("正文状态") or "尚未试跑",
                })
                continue
            recommendations.setdefault(category, []).append({
                "template_name": row.get("采纳模板"),
                "page_uri": row.get("页面URI"),
                "body_hash": row.get("正文哈希"),
                "profile_id": row.get("门槛口径"),
                "receipt_id": row.get("试跑回执ID"),
            })
        return {
            "ok": True,
            "classified_pages": len(rows),
            "template_recommendations": recommendations,
            "blocked": blocked,
        }

    def compute_classification_stats(self) -> dict[str, Any]:
        """分类统计。"""
        rows = store.rows(self.MODULE)
        cat_dist: dict[str, int] = {}
        reviewed = 0
        for row in rows:
            cat = row.get("一级分类", "未分类")
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
            if row.get("status") == "已复核":
                reviewed += 1
        return {
            "total_labels": len(rows),
            "category_distribution": cat_dist,
            "reviewed_count": reviewed,
            "review_ratio": round(reviewed / len(rows), 2) if rows else 0.0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["classification_stats"] = self.compute_classification_stats()
        return base