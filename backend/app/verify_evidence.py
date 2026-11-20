"""正文证据贯穿链路的验收脚本。

覆盖：按 URI 无正文时不得分类为可采集类别、正文证据门槛、同页双类别冲突裁定、
试跑回执解释、未采纳证据按正文片段回查、规则调整后重算保留旧依据、
缺正文不得进入可采集模板集合。

运行：cd backend && .venv/bin/python -m app.verify_evidence
"""
from __future__ import annotations

import json

from app.services.content_classifier import ContentClassifierService
from app.services.evidence_rules import (
    DEFAULT_PROFILE,
    DEMO_BODIES,
    DEMO_PAGE_URI,
)
from app.services.template_rule import TemplateRuleService
from app.store import store

classifier = ContentClassifierService()
templates = TemplateRuleService()

failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" | {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def body_of(key: str) -> str:
    return next(item["body"] for item in DEMO_BODIES if item["key"] == key)


def main() -> int:
    rows_before = {name: len(store.rows(name)) for name in (
        "content_classifier", "template_rule", "page_snapshot", "text_segment")}

    print("\n== 1. 同一 URI 两份不同类别正文 ==")
    news = judge(body_of("news"), "新闻正文")
    product = judge(body_of("product"), "商品正文")
    check("新闻正文分类为新闻媒体",
          news["dataset"]["classification"]["primary_category"] == "新闻媒体",
          news["dataset"]["classification"])
    check("商品正文分类为电子商务",
          product["dataset"]["classification"]["primary_category"] == "电子商务",
          product["dataset"]["classification"])
    check("两份正文的候选类别互不相同",
          {c["category"] for c in news["dataset"]["candidates"] if c["qualified"]}
          != {c["category"] for c in product["dataset"]["candidates"] if c["qualified"]})
    check("新闻试跑抽到新闻字段",
          {f["field"] for f in news["samples"][0]["fields"]} == {"标题", "发布时间", "作者", "正文首段"})
    check("商品试跑抽到商品字段",
          {f["field"] for f in product["samples"][0]["fields"]} == {"商品名称", "价格", "库存状态", "店铺"})
    check("新闻试跑字段带正文出处",
          all(f["status"] != "命中" or f["quote"] for f in news["samples"][0]["fields"]))

    print("\n== 2. 含两类证据的混合正文 ==")
    mixed = judge(body_of("mixed"), "混合正文")
    qualified = [c for c in mixed["dataset"]["candidates"] if c["qualified"]]
    check("混合正文同时命中两个类别门槛", {c["category"] for c in qualified} == {"新闻媒体", "电子商务"},
          [c["category"] for c in qualified])
    check("两个模板各生成试跑样本", len(mixed["samples"]) == 2,
          [s["template_name"] for s in mixed["samples"]])
    news_fields = next(s for s in mixed["samples"] if s["category"] == "新闻媒体")
    product_fields = next(s for s in mixed["samples"] if s["category"] == "电子商务")
    check("新闻与商品模板抽出不同字段",
          {f["field"] for f in news_fields["fields"]} != {f["field"] for f in product_fields["fields"]})
    receipt = mixed["trial_receipt"]
    check("冲突裁定采纳唯一模板", receipt["adopted_template"] == "新闻资讯模板", receipt["adopted_template"])
    check("回执写明裁定规则", bool(receipt["conflict_rule"]) and bool(receipt["adopt_reason"]),
          receipt["conflict_rule"])
    check("回执可解释未采纳模板", any("暂不采纳" in note for note in receipt["decision_notes"]),
          receipt["decision_notes"])
    trace = receipt["evidence_trace"]
    check("未采纳候选的证据仍在回执里", any(item["accepted"] is False and item["quote"] for item in trace))

    print("\n== 3. 试跑回执与正文片段回查 ==")
    stored = templates.get_trial_receipt(receipt["receipt_id"])
    check("回执已随分类审核清单落库", stored is not None)
    check("回查能定位新闻证据片段",
          stored is not None and any(item["category"] == "新闻媒体" and item["quote"] for item in stored["evidence_trace"]))
    check("回查能定位商品证据片段",
          stored is not None and any(item["category"] == "电子商务" and item["quote"] for item in stored["evidence_trace"]))
    conflict = templates.resolve_conflict_status(receipt)
    check("冲突结论压缩为已采纳/待复核/被拒",
          conflict["conflict"] and conflict["pending_review"] and conflict["accepted"] == ["新闻资讯模板"],
          conflict)

    print("\n== 4. 缺正文与低于门槛不得进入可采集模板集合 ==")
    empty = judge(body_of("empty"), "正文缺失")
    check("缺正文时不产生候选", not empty["dataset"]["candidates"] and not empty["dataset"]["body_valid"])
    check("缺正文时不生成试跑采样", empty["samples"] == [])
    check("缺正文审核项标记待复核", empty["trial_receipt"]["review_status"] == "待复核",
          empty["trial_receipt"]["review_status"])
    check("缺正文时分类为未分类",
          classifier.classify_page(DEMO_PAGE_URI + "?empty", body_of("empty"))
          ["一级分类"] == "未分类")
    check("仅凭 URI 不再给出可采集分类",
          classifier.classify_page("https://demo.example.com/unknown")["正文状态"] == "正文缺失")

    weak = templates.run_template_trial(
        DEMO_PAGE_URI, "标题：页面摘要\n正文很短，暂时还没有形成完整证据。",
        source="verify", sync_review=False, profile_id="gate-2026.11-v2")
    check("低于正文门槛的正文不生成候选", weak["ok"] is False and weak["samples"] == [])

    collectable = templates.get_collectable_templates()
    names = {item["template_name"] for item in collectable["collectable"]}
    check("可采集集合只含通过门槛的模板",
          all(item["body_hash"] and item["status"] in ("草稿", "已验证", "已发布")
              for item in collectable["collectable"]),
          collectable["collectable"])
    check("缺正文页面没有模板进入可采集集合", not any("empty" in str(name) for name in names))

    print("\n== 5. 规则调整后重算保留旧样本依据 ==")
    v1 = templates.run_template_trial(
        DEMO_PAGE_URI, body_of("borderline"), source="verify",
        profile_id="gate-2026.11-v1", sync_review=True)
    v2 = templates.run_template_trial(
        DEMO_PAGE_URI, body_of("borderline"), source="verify",
        profile_id="gate-2026.11-v2", sync_review=True)
    check("v1 门槛下边缘正文被采纳", v1["ok"] and v1["adopted_candidate"] is not None)
    check("v2 收紧后边缘正文低于门槛", v2["ok"] is False and v2["samples"] == [])
    history = templates.list_candidate_dataset(DEMO_PAGE_URI)
    check("同一页面保留了两个正文版本", history["version_count"] >= 2, history["version_count"])
    old_version = next(
        (item for item in history["versions"] if item["profile_id"] == "gate-2026.11-v1"), None)
    check("旧版本仍保留当时的判定依据",
          old_version is not None and old_version["trial_status"] == "已采纳"
          and old_version["body_hash"],
          old_version)

    print("\n== 6. 同版正文与门槛口径运行分类与模板验证 ==")
    for key in ("news", "product", "mixed", "borderline"):
        body = body_of(key)
        classified = classifier.classify_page(DEMO_PAGE_URI, body, DEFAULT_PROFILE)
        dataset = templates.build_candidate_dataset(DEMO_PAGE_URI, body, profile_id=DEFAULT_PROFILE)
        check(f"{key}: 分类与候选口径一致",
              classified["门槛口径"] == dataset["profile_id"]
              and classified["正文状态"] == dataset["body_status"]
              and classified["一级分类"] == dataset["classification"]["primary_category"],
              (classified["一级分类"], dataset["classification"]["primary_category"]))

    print("\n== 7. 来源片段同步落库 ==")
    rows_after = {name: len(store.rows(name)) for name in rows_before}
    check("分类审核清单新增了记录", rows_after["content_classifier"] > rows_before["content_classifier"])
    check("模板规则新增了草稿模板", rows_after["template_rule"] > rows_before["template_rule"])
    check("页面快照新增了正文快照", rows_after["page_snapshot"] > rows_before["page_snapshot"])
    check("文本分块新增了来源片段", rows_after["text_segment"] > rows_before["text_segment"])
    snippets = [
        row for row in store.rows("text_segment")
        if row.get("来源页面") == DEMO_PAGE_URI and row.get("正文哈希")
    ]
    check("片段带正文哈希与回执号", all(row.get("试跑回执ID") for row in snippets))

    print("\n== 8. 幂等重试 ==")
    again = templates.run_template_trial(
        DEMO_PAGE_URI, body_of("mixed"), source="verify", sync_review=True)
    check("同正文同口径重跑不新增版本", again["review_entry"]["reused"] is True)

    print()
    if failures:
        print(f"{len(failures)} 项未通过：")
        for item in failures:
            print(" -", item)
        return 1
    print("全部校验通过")
    return 0


def judge(body: str, label: str) -> dict:
    result = templates.run_template_trial(DEMO_PAGE_URI, body, source="verify")
    print(f"-- {label}: ok={result['ok']} result={result.get('result') or result.get('message')}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
