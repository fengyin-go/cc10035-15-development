"""正文证据接口：采集模板资格校验、模板候选依据数据集、试跑与回执回查。

路径统一挂 /api/evidence 前缀，避免和通用资源路由 /api/{module}/{entry_id}
的动态段抢匹配；业务判断全部留在 services 层。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import evidence_rules
from app.services.content_classifier import ContentClassifierService
from app.services.template_rule import TemplateRuleService

router = APIRouter(prefix="/api/evidence", tags=["正文证据"])

classifier_service = ContentClassifierService()
template_service = TemplateRuleService()


class EvidencePayload(BaseModel):
    page_uri: str
    body: str = ""
    profile_id: str | None = None
    source: str = ""


class TrialPayload(EvidencePayload):
    category: str | None = None
    sync_review: bool = True


@router.get("/profiles")
def list_profiles() -> dict[str, Any]:
    """可用门槛口径与模板字段口径。"""
    return {
        "ok": True,
        "default_profile": evidence_rules.DEFAULT_PROFILE,
        "profiles": evidence_rules.list_profiles(),
        "templates": [
            {
                "category": category,
                "template_name": spec["template_name"],
                "fields": [field["field"] for field in spec["fields"]],
                "min_coverage": spec["min_coverage"],
            }
            for category, spec in evidence_rules.TEMPLATES.items()
        ],
    }


@router.get("/samples")
def demo_samples() -> dict[str, Any]:
    """演示样本：同一 URI 的新闻正文、商品正文、混合正文与缺正文样本。"""
    payload = evidence_rules.demo_payload()
    payload["ok"] = True
    return payload


@router.post("/classify")
def classify(payload: EvidencePayload) -> dict[str, Any]:
    """用正文证据分类，替代按 URI 标签的方式。"""
    return classifier_service.classify_page(
        payload.page_uri, payload.body, payload.profile_id)


@router.post("/datasets")
def build_dataset(payload: EvidencePayload) -> dict[str, Any]:
    """校验正文并生成模板候选依据数据集（不落库）。"""
    return template_service.build_candidate_dataset(
        payload.page_uri, payload.body,
        source=payload.source or "api", profile_id=payload.profile_id)


@router.post("/trials")
def run_trial(payload: TrialPayload) -> dict[str, Any]:
    """按候选依据数据集试跑字段抽取，回写审核清单并同步来源片段。"""
    return template_service.run_template_trial(
        payload.page_uri, payload.body,
        source=payload.source or "api",
        profile_id=payload.profile_id,
        category=payload.category,
        sync_review=payload.sync_review,
    )


@router.get("/trials/{receipt_id}")
def get_trial_receipt(receipt_id: str) -> dict[str, Any]:
    """按回执 ID 回查，含当期未采纳证据的正文片段。"""
    receipt = template_service.get_trial_receipt(receipt_id)
    if receipt is None:
        return {"ok": False, "message": f"试跑回执 {receipt_id} 不存在"}
    return {"ok": True, **receipt}


@router.get("/collectable")
def collectable_templates() -> dict[str, Any]:
    """可采集模板集合：只包含正文通过门槛且已被采纳的模板。"""
    return template_service.get_collectable_templates()


@router.get("/candidates")
def list_candidates(
    page_uri: str = Query(default=""),
    profile_id: str | None = Query(default=None),
    qualified_only: bool = Query(default=False),
) -> dict[str, Any]:
    """分类审核清单里落库的模板候选依据数据集。"""
    rows = template_service.list_template_candidates(
        page_uri=page_uri, profile_id=profile_id, qualified_only=qualified_only)
    return {"ok": True, "items": rows, "total": len(rows)}


@router.get("/dataset")
def page_dataset(page_uri: str = Query(min_length=1)) -> dict[str, Any]:
    """按页面回查各正文版本的候选依据与可采集资格。"""
    return template_service.list_candidate_dataset(page_uri)
