"""模板规则领域服务：管理站点级字段抽取模板。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services import evidence_rules
from app.services.base import BaseModuleService
from app.store import store


class TemplateRuleService(BaseModuleService):
    MODULE = "template_rule"
    ENTITY = "规则模板"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "模板名称", "适用站点", "提取字段", "字段选择器", "采样页面", "校验规则", "更新策略",
        "来源候选ID", "试跑回执ID", "正文哈希", "门槛口径",
    ]
    REQUIRED_FIELDS = ["模板名称", "适用站点", "提取字段"]
    STATUS_ORDER = ["草稿", "已验证", "已发布", "已失效"]
    ACTION_RULES = {"验证模板": "草稿", "发布模板": "已验证", "标记失效": "已发布"}
    NEGATIVE_ACTIONS = []

    def suggest_probes(self, template_id: int) -> dict[str, Any]:
        """根据模板规则推导建议的探针配置，供探针选择模块消费。"""
        template = store.find(self.MODULE, template_id)
        if template is None:
            return {"ok": False, "reason": "模板不存在"}
        site = template.get("适用站点", "")
        selectors_str = template.get("字段选择器", "")
        sample_page = template.get("采样页面", "")
        selectors = [s.strip() for s in selectors_str.split(",") if s.strip()]

        probes = []
        for i, sel in enumerate(selectors):
            page = sample_page if sample_page else site
            probes.append({
                "探针名称": f"{template.get('模板名称')}-probe-{i+1}",
                "目标页面": page,
                "选择器列表": sel,
                "验证关键字": "",
                "采样频率": "daily",
                "超时秒数": "30",
                "重试次数": "3",
            })
        return {
            "ok": True,
            "template_id": template_id,
            "template_name": template.get("模板名称"),
            "suggested_probe_count": len(probes),
            "probes": probes,
        }

    def compute_template_coverage(self) -> dict[str, Any]:
        """计算模板覆盖范围，供抽取指标消费。"""
        rows = store.rows(self.MODULE)
        sites: dict[str, int] = {}
        total_selectors = 0
        total_fields = 0
        for row in rows:
            site = row.get("适用站点", "")
            sites[site] = sites.get(site, 0) + 1
            total_selectors += len(row.get("字段选择器", "").split(",")) if row.get("字段选择器", "") else 0
            total_fields += len(row.get("提取字段", "").split(",")) if row.get("提取字段", "") else 0
        return {
            "template_count": len(rows),
            "site_count": len(sites),
            "total_selectors": total_selectors,
            "total_target_fields": total_fields,
            "published": sum(1 for r in rows if r.get("status") == "已发布"),
        }

    def get_published_templates(self) -> list[dict[str, Any]]:
        """取出所有已发布模板，供质量检查跨模块比对。"""
        return [
            {
                "id": r.get("id"),
                "name": r.get("模板名称"),
                "site": r.get("适用站点"),
                "fields": r.get("提取字段"),
                "selectors": r.get("字段选择器"),
                "sample_page": r.get("采样页面"),
            }
            for r in store.rows(self.MODULE)
            if r.get("status") == "已发布"
        ]

    def build_candidate_dataset(
        self,
        page_uri: str,
        body: str,
        *,
        source: str = "",
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """校验页面正文并生成“模板候选依据数据集”。

        数据集是不落库的产物：正文缺失或低于模块门槛时，所有候选都标记为
        不可采集，qualified=false，调用方不能把它们当作可采集模板。
        """
        try:
            profile = evidence_rules.get_profile(profile_id)
        except KeyError as exc:
            return {"ok": False, "reason": str(exc)}
        evaluation = evidence_rules.evaluate_body(body, profile["profile_id"])
        classification = evidence_rules.classify(evaluation, body)
        content = evidence_rules.normalize_body(body)
        body_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if content else ""

        candidates: list[dict[str, Any]] = []
        for item in evaluation["candidates"]:
            candidate_id = "tplcand-" + hashlib.sha256(
                f"{page_uri}|{body_hash}|{item['category']}|{profile['profile_id']}".encode()
            ).hexdigest()[:12]
            qualified = bool(item["qualified"])
            candidates.append({
                "candidate_id": candidate_id,
                "page_uri": page_uri,
                "category": item["category"],
                "template_name": item["template_name"],
                "template_family": item["template_family"],
                "profile_id": profile["profile_id"],
                "body_hash": body_hash,
                "body_char_count": evaluation["char_count"],
                "body_status": evaluation["body_status"],
                "evidence_score": item["evidence_score"],
                "threshold": item["threshold"],
                "min_signals": item["min_signals"],
                "matched_signal_count": item["matched_signal_count"],
                "matched_signals": item["matched_signals"],
                "gate": item["gate"],
                "gate_reason": item["gate_reason"],
                "qualified": qualified,
                "collectable": qualified,
                "block_reason": "" if qualified else self._block_reason(evaluation, item),
            })

        ranked = evidence_rules.rank_candidates(candidates)
        qualified_rows = [item for item in ranked if item["qualified"]]
        return {
            "ok": True,
            "page_uri": page_uri,
            "source": source,
            "profile_id": profile["profile_id"],
            "profile_label": profile["label"],
            "body_hash": body_hash,
            "body_status": evaluation["body_status"],
            "body_reason": evaluation["body_reason"],
            "body_valid": evaluation["body_valid"],
            "char_count": evaluation["char_count"],
            "classification": classification,
            "candidates": ranked,
            "qualified_candidate_ids": [item["candidate_id"] for item in qualified_rows],
            "rejected_candidate_ids": [item["candidate_id"] for item in ranked if not item["qualified"]],
            "dataset_digest": hashlib.sha256(
                "|".join(f"{item['candidate_id']}:{item['qualified']}" for item in ranked).encode()
            ).hexdigest()[:16],
        }

    @staticmethod
    def _block_reason(evaluation: dict[str, Any], item: dict[str, Any]) -> str:
        if not evaluation["body_valid"]:
            return evaluation["body_reason"]
        return item["gate_reason"]

    def run_template_trial(
        self,
        page_uri: str,
        body: str,
        *,
        source: str = "",
        profile_id: str | None = None,
        category: str | None = None,
        sync_review: bool = True,
    ) -> dict[str, Any]:
        """按模板候选依据数据集试跑字段抽取，并给出冲突裁定回执。

        只对通过正文证据门槛的候选生成试跑样本；同一页面命中多个类别时，
        逐候选试跑后按得分与类别优先级裁定主模板，其余候选标记为暂不采纳，
        但仍保留正文出处片段，便于按片段回查。
        """
        dataset = self.build_candidate_dataset(
            page_uri, body, source=source, profile_id=profile_id)
        if not dataset.get("ok"):
            return dataset
        qualified = [item for item in dataset["candidates"] if item["qualified"]]
        if category:
            qualified = [item for item in qualified if item["category"] == category]
            dataset["category_filter"] = category

        if not qualified:
            receipt = self._build_receipt(dataset, [], adopted=None, page_uri=page_uri)
            review_entry = None
            if sync_review:
                receipt = self._attach_segments(receipt, [], None)
                review_entry = self._write_back_review(
                    page_uri=page_uri, dataset=dataset, samples=[], adopted=None,
                    receipt=receipt)
                receipt["review_writeback"] = review_entry
            return {
                "ok": False,
                "message": "没有通过正文证据门槛的模板候选，未生成任何试跑样本",
                "dataset": dataset,
                "samples": [],
                "adopted_candidate": None,
                "trial_receipt": receipt,
                "review_entry": review_entry,
            }

        content = evidence_rules.normalize_body(body)
        samples: list[dict[str, Any]] = []
        for item in qualified:
            fields = evidence_rules.extract_fields(content, item["category"])
            hit_count = sum(1 for field in fields if field["status"] == "命中")
            total = len(fields)
            coverage = round(hit_count / total, 2) if total else 0.0
            min_coverage = evidence_rules.TEMPLATES[item["category"]]["min_coverage"]
            selectors = evidence_rules.TEMPLATES[item["category"]]["selectors"]
            field_rows = []
            for field in fields:
                field_rows.append({
                    **field,
                    "selector": selectors.get(field["field"], ""),
                    "quote": field.get("quote"),
                    "body_hash": dataset["body_hash"],
                    "page_uri": page_uri,
                })
            samples.append({
                "candidate_id": item["candidate_id"],
                "template_name": item["template_name"],
                "category": item["category"],
                "template_family": item["template_family"],
                "profile_id": dataset["profile_id"],
                "body_hash": dataset["body_hash"],
                "evidence_score": item["evidence_score"],
                "threshold": item["threshold"],
                "gate": item["gate"],
                "gate_reason": item["gate_reason"],
                "field_count": total,
                "hit_count": hit_count,
                "field_coverage": coverage,
                "min_coverage": min_coverage,
                "trial_status": "命中" if coverage >= min_coverage else "字段不完整",
                "fields": field_rows,
            })

        adopted, decision_log = self._resolve_conflict(samples)
        receipt = self._build_receipt(
            dataset, samples, adopted=adopted, page_uri=page_uri,
            extra_notes=decision_log, category_filter=category)

        review_entry = None
        if sync_review:
            receipt = self._attach_segments(receipt, samples, adopted)
            review_entry = self._write_back_review(
                page_uri=page_uri,
                dataset=dataset,
                samples=samples,
                adopted=adopted,
                receipt=receipt,
            )
            receipt["review_writeback"] = review_entry
        return {
            "ok": True,
            "result": "已采纳" if adopted else "全部待复核",
            "dataset": dataset,
            "samples": samples,
            "adopted_candidate": adopted,
            "trial_receipt": receipt,
            "review_entry": review_entry,
        }

    def _resolve_conflict(
        self, samples: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """同页多候选时裁定唯一采纳项，并把裁定理由写进回执。"""
        if not samples:
            return None, []
        if len(samples) == 1:
            only = samples[0]
            return (
                dict(only, adopt_reason="唯一通过正文证据门槛的候选，直接采纳"),
                [f"仅「{only['template_name']}」通过正文证据门槛，无规则冲突"],
            )
        ranked = evidence_rules.rank_candidates(samples)
        top, runner_up = ranked[0], ranked[1]
        notes = [
            f"同一页面命中 {len(samples)} 个正文证据门槛："
            + "、".join(f"{item['category']}({item['evidence_score']:.2f})" for item in ranked),
        ]
        if abs(float(top["evidence_score"]) - float(runner_up["evidence_score"])) > 1e-9:
            reason = (
                f"证据得分 {top['evidence_score']:.2f} 高于「{runner_up['template_name']}」的 "
                f"{runner_up['evidence_score']:.2f}，按得分优先采纳"
            )
            rule = "得分优先"
        elif top["field_coverage"] != runner_up["field_coverage"]:
            reason = (
                f"证据得分相同，按字段覆盖率裁定：{top['field_coverage']:.2f} > "
                f"{runner_up['field_coverage']:.2f}"
            )
            rule = "字段覆盖率优先"
        elif top["field_count"] != runner_up["field_count"]:
            reason = (
                f"得分与覆盖率相同，按模板字段数裁定：{top['field_count']} > "
                f"{runner_up['field_count']}"
            )
            rule = "字段数优先"
        else:
            reason = (
                f"得分、覆盖率均相同，按类别稳定优先级裁定"
                f"（{top['category']} 优先于 {runner_up['category']}）"
            )
            rule = "类别优先级兜底"
        notes.append(f"裁定规则：{rule}；采纳「{top['template_name']}」，其余候选暂不采纳")
        missing = [f["field"] for f in top["fields"] if f["status"] != "命中"]
        if missing:
            notes.append("采纳模板仍有字段未命中，需补齐选择器：" + "、".join(missing))
        for item in ranked:
            if item is top:
                continue
            others = [f["field"] for f in item["fields"] if f["status"] != "命中"]
            if others:
                notes.append(
                    f"未采纳「{item['template_name']}」仍需复核：字段未匹配 "
                    + "、".join(others)
                )
        return (
            dict(top, adopt_reason=reason, conflict_rule=rule),
            notes,
        )

    def _build_receipt(
        self,
        dataset: dict[str, Any],
        samples: list[dict[str, Any]],
        *,
        adopted: dict[str, Any] | None,
        page_uri: str,
        extra_notes: list[str] | None = None,
        category_filter: str | None = None,
    ) -> dict[str, Any]:
        by_category = {item["category"]: item for item in samples}
        adopted_category = adopted["category"] if adopted else None
        evidence: list[dict[str, Any]] = []
        for candidate in dataset["candidates"]:
            sample = by_category.get(candidate["category"])
            accepted = bool(sample is not None and adopted_category == candidate["category"])
            if candidate["qualified"]:
                gate_note = (
                    f"通过正文证据门槛（{candidate['evidence_score']:.2f} ≥ "
                    f"{candidate['threshold']:.2f}）"
                )
                if adopted and candidate["category"] == adopted_category:
                    gate_note += "；按冲突裁定采纳"
                elif sample is not None:
                    gate_note += "；按冲突裁定暂不采纳"
                else:
                    gate_note += "；未生成试跑样本"
            else:
                gate_note = f"未通过正文证据门槛：{candidate['gate_reason']}"
            evidence.append({
                "candidate_id": candidate["candidate_id"],
                "category": candidate["category"],
                "template_name": candidate["template_name"],
                "evidence_score": candidate["evidence_score"],
                "threshold": candidate["threshold"],
                "gate": candidate["gate"],
                "gate_reason": candidate["gate_reason"],
                "qualified": candidate["qualified"],
                "accepted": accepted,
                "gate_note": gate_note,
                "body_hash": candidate["body_hash"],
                "profile_id": candidate["profile_id"],
                "matched_signals": candidate["matched_signals"],
                "trial_status": sample["trial_status"] if sample else "未试跑",
                "field_coverage": sample["field_coverage"] if sample else None,
                "review_status": "已采纳" if accepted else "待复核",
            })
        notes = list(extra_notes or [])
        if not dataset["body_valid"]:
            notes.insert(0, f"正文不可用：{dataset['body_reason']}，未生成可采集模板候选")
        if category_filter:
            notes.append(f"仅试跑分类「{category_filter}」对应的候选")
        if not samples and dataset["body_valid"]:
            notes.append("正文有效但没有类别达到证据门槛，模板候选全部待补齐正文证据")
        receipt = {
            "receipt_id": "trial-" + hashlib.sha256(
                f"{page_uri}|{dataset['dataset_digest']}|{dataset['profile_id']}|"
                f"{adopted_category or 'none'}".encode()
            ).hexdigest()[:12],
            "page_uri": page_uri,
            "profile_id": dataset["profile_id"],
            "body_hash": dataset["body_hash"],
            "body_status": dataset["body_status"],
            "classification": dataset["classification"],
            "adopted_template": adopted["template_name"] if adopted else "",
            "adopted_category": adopted_category or "",
            "adopt_reason": adopted.get("adopt_reason", "") if adopted else "",
            "conflict_rule": adopted.get("conflict_rule", "") if adopted else "",
            "evidence": evidence,
            "rejected_evidence": [
                {
                    "candidate_id": item["candidate_id"],
                    "category": item["category"],
                    "gate_reason": item["gate_reason"],
                    "matched_signals": item["matched_signals"],
                    "body_hash": item["body_hash"],
                }
                for item in dataset["candidates"] if not item["qualified"]
            ],
            "decision_notes": notes,
            "review_status": "已同步审核清单" if adopted else "待复核",
        }
        return receipt

    @staticmethod
    def _attach_segments(
        receipt: dict[str, Any],
        samples: list[dict[str, Any]],
        adopted: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """从采纳样本（或待复核样本）中挑出带正文出处的片段，供落库回查。"""
        source = adopted or (samples[0] if samples else None)
        segments: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        if source is not None:
            for field in source["fields"]:
                if field["status"] != "命中" or field.get("quote") is None:
                    continue
                span = (int(field["quote_start"]), int(field["quote_end"]))
                if span in seen:
                    continue
                seen.add(span)
                segments.append({
                    "block_type": "paragraph",
                    "field": field["field"],
                    "quote_start": span[0],
                    "quote_end": span[1],
                    "quote": field["quote"],
                })
        if not segments:
            for item in receipt["rejected_evidence"]:
                for signal in item["matched_signals"][:1]:
                    segments.append({
                        "block_type": "evidence",
                        "field": signal["signal_id"],
                        "quote_start": signal["quote_start"],
                        "quote_end": signal["quote_end"],
                        "quote": signal["quote"],
                    })
                    break
                if segments:
                    break
        updated = dict(receipt)
        updated["accepted_segments"] = segments
        updated["evidence_trace"] = [
            {
                "category": item["category"],
                "signal_id": signal["signal_id"],
                "signal_label": signal["signal_label"],
                "weight": signal["weight"],
                "body_hash": item["body_hash"],
                "quote_start": signal["quote_start"],
                "quote_end": signal["quote_end"],
                "quote": signal["quote"],
                "accepted": item["accepted"],
            }
            for item in receipt["evidence"]
            for signal in item["matched_signals"]
        ] or [
            {
                "category": item["category"],
                "signal_id": "",
                "signal_label": "",
                "weight": 0.0,
                "body_hash": item["body_hash"],
                "quote_start": 0,
                "quote_end": 0,
                "quote": "",
                "accepted": False,
                "reason": item["gate_reason"],
            }
            for item in receipt["rejected_evidence"]
        ]
        return updated

    def _write_back_review(
        self,
        *,
        page_uri: str,
        dataset: dict[str, Any],
        samples: list[dict[str, Any]],
        adopted: dict[str, Any] | None,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """把试跑回执回写到分类审核清单，并新建草稿模板 + 快照 + 文本块。

        同一个页面、正文版本、门槛口径和采纳结果只写一次；规则调整后重算时
        不覆盖旧记录，而是追加新版本，旧样本的判定依据得以保留。
        """
        classifier = store.rows("content_classifier")
        same_page = [row for row in classifier if row.get("页面URI") == page_uri]
        version = max(
            [int(row.get("正文版本", 0) or 0) for row in same_page], default=0
        ) + 1
        classification = dataset["classification"]
        existing_version = next(
            (
                row for row in same_page
                if row.get("正文哈希") == dataset["body_hash"]
                and row.get("门槛口径") == dataset["profile_id"]
            ),
            None,
        )
        entry = existing_version or {}
        if existing_version is None:
            entry = {
                "id": max((int(row.get("id", 0)) for row in classifier), default=0) + 1,
                "标签ID": "cls-" + hashlib.sha256(
                    f"{page_uri}|{dataset['body_hash']}|{dataset['profile_id']}".encode()
                ).hexdigest()[:12],
                "页面URI": page_uri,
                "正文版本": version,
                "正文哈希": dataset["body_hash"],
                "正文状态": dataset["body_status"],
                "门槛口径": dataset["profile_id"],
            }
            classifier.append(entry)
        entry.update({
            "status": "已分类" if classification["primary_category"] != "未分类" else "待分类",
            "pending": classification["primary_category"] == "未分类",
            "abnormal": not dataset["body_valid"] or not adopted,
            "一级分类": classification["primary_category"],
            "二级分类": classification["secondary_category"],
            "置信度": f"{classification['confidence']:.2f}",
            "分类依据": classification["basis"],
            "标注时间": "2026-11-20T10:00:00Z",
            "正文版本": version if existing_version is None else entry.get("正文版本", version),
            "正文哈希": dataset["body_hash"],
            "正文状态": dataset["body_status"],
            "门槛口径": dataset["profile_id"],
            "模板候选ID": ",".join(dataset["qualified_candidate_ids"]) or "—",
            "采纳模板": adopted["template_name"] if adopted else "—",
            "试跑回执ID": receipt["receipt_id"],
            "试跑结果": (
                "已采纳" if adopted else ("正文不可用" if not dataset["body_valid"] else "全部待复核")
            ),
            "正文出处": self._snippet_index(receipt),
            "正文片段": self._snippet_text(receipt, samples, adopted),
            "证据回查": "；".join(
                f"{item['category']}/{item['signal_id']}：{item['quote'][:40]}"
                for item in receipt.get("evidence_trace", [])[:4]
            ) or "—",
            # 结构化轨迹随审核清单落库，回查时不用再解字符串。
            "正文证据轨迹": json.dumps(
                receipt.get("evidence_trace", []), ensure_ascii=False),
        })
        note = "；".join(receipt["decision_notes"]) or "无冲突"
        if existing_version is not None and existing_version.get("审核备注") not in (None, "", note):
            # 同一正文与口径重算后判定有变化：旧备注保留在重算历史里，不直接覆盖。
            entry["重算历史"] = " | ".join(
                filter(None, [str(entry.get("重算历史", "")), str(existing_version.get("审核备注", ""))]))
        entry["审核备注"] = note

        template_row = None
        if adopted is not None:
            template_row = self._upsert_draft_template(
                page_uri=page_uri, dataset=dataset, adopted=adopted, receipt=receipt)
            entry["生成模板ID"] = template_row["id"]

        snapshot_row = self._sync_snapshot(page_uri, dataset, receipt)
        segment_rows = self._sync_text_segments(page_uri, dataset, receipt)
        return {
            "entry_id": entry["id"],
            "tag_id": entry["标签ID"],
            "status": entry["status"],
            "body_version": entry["正文版本"],
            "reused": existing_version is not None,
            "template_id": template_row["id"] if template_row else None,
            "snapshot_id": snapshot_row["id"],
            "text_segment_ids": [row["id"] for row in segment_rows],
        }

    @staticmethod
    def _snippet_text(
        receipt: dict[str, Any],
        samples: list[dict[str, Any]],
        adopted: dict[str, Any] | None,
    ) -> str:
        if adopted is not None:
            quotes = [
                f"{field['field']}={field['value']}"
                for field in adopted["fields"] if field["status"] == "命中"
            ]
            return "；".join(quotes) or "无命中字段"
        parts = []
        for sample in samples:
            parts.append(
                f"{sample['template_name']}({sample['trial_status']}):"
                + "、".join(
                    field["field"] for field in sample["fields"] if field["status"] == "命中"
                )
            )
        if parts:
            return "；".join(parts)
        for item in receipt["rejected_evidence"]:
            if item["matched_signals"]:
                return item["matched_signals"][0]["quote"]
        return "无正文证据片段"

    @staticmethod
    def _snippet_index(receipt: dict[str, Any]) -> str:
        spans = []
        for item in receipt["evidence"]:
            for signal in item["matched_signals"]:
                spans.append(f"{item['category']}:{signal['signal_id']}@{signal['quote_start']}")
        return ",".join(spans) or "—"

    def _upsert_draft_template(
        self,
        *,
        page_uri: str,
        dataset: dict[str, Any],
        adopted: dict[str, Any],
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """采纳结果落成草稿模板；按页面+正文版本+口径幂等，不覆盖旧版本。"""
        rows = store.rows(self.MODULE)
        name = (
            f"{adopted['template_name']}-{page_uri.rstrip('/').split('/')[-1]}"
            f"-{dataset['profile_id']}"
        )
        existing = next((row for row in rows if row.get("模板名称") == name), None)
        if existing is not None:
            return existing
        fields = evidence_rules.TEMPLATES[adopted["category"]]["fields"]
        selectors = evidence_rules.TEMPLATES[adopted["category"]]["selectors"]
        row = {
            "id": max((int(item.get("id", 0)) for item in rows), default=0) + 1,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "模板名称": name,
            "适用站点": page_uri,
            "提取字段": ", ".join(field["field"] for field in fields),
            "字段选择器": ", ".join(selectors.get(field["field"], "") for field in fields),
            "采样页面": page_uri,
            "校验规则": f"正文证据得分 ≥ {adopted['threshold']}，字段覆盖率 ≥ {adopted['min_coverage']}",
            "更新策略": f"正文版本 {dataset['body_hash'][:8]} / 门槛口径 {dataset['profile_id']}",
            "来源候选ID": adopted["candidate_id"],
            "试跑回执ID": receipt["receipt_id"],
            "证据得分": str(adopted["evidence_score"]),
            "正文哈希": dataset["body_hash"],
            "门槛口径": dataset["profile_id"],
        }
        rows.append(row)
        return row

    def _sync_snapshot(
        self, page_uri: str, dataset: dict[str, Any], receipt: dict[str, Any]
    ) -> dict[str, Any]:
        rows = store.rows("page_snapshot")
        existing = next(
            (
                row for row in rows
                if row.get("页面URI") == page_uri and row.get("文本哈希") == dataset["body_hash"]
            ),
            None,
        )
        if existing is not None:
            return existing
        dom_hash = "dom-" + hashlib.md5(page_uri.encode()).hexdigest()[:12]
        row = {
            "id": max((int(item.get("id", 0)) for item in rows), default=0) + 1,
            "status": "已保存",
            "pending": True,
            "abnormal": not dataset["body_valid"],
            "快照ID": "snap-" + dataset["body_hash"][:12],
            "页面URI": page_uri,
            "DOM哈希": dom_hash,
            "文本哈希": dataset["body_hash"],
            "抓取时间": "2026-11-20T10:00:00Z",
            "页面大小": str(dataset["char_count"]),
            "是否变更": "是",
            "试跑回执ID": receipt["receipt_id"],
        }
        rows.append(row)
        return row

    def _sync_text_segments(
        self, page_uri: str, dataset: dict[str, Any], receipt: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rows = store.rows("text_segment")
        segments = []
        for segment in receipt.get("accepted_segments", []):
            block_id = "seg-" + hashlib.md5(
                f"{page_uri}|{dataset['body_hash']}|{segment['quote_start']}".encode()
            ).hexdigest()[:10]
            existing = next((row for row in rows if row.get("块ID") == block_id), None)
            if existing is not None:
                segments.append(existing)
                continue
            row = {
                "id": max((int(item.get("id", 0)) for item in rows), default=0) + 1,
                "status": "已切分",
                "pending": True,
                "abnormal": False,
                "块ID": block_id,
                "来源页面": page_uri,
                "块类型": segment["block_type"],
                "起始位置": str(segment["quote_start"]),
                "结束位置": str(segment["quote_end"]),
                "字符长度": str(segment["quote_end"] - segment["quote_start"]),
                "语言标识": "zh-CN",
                "text_sample": segment["quote"],
                "正文哈希": dataset["body_hash"],
                "门槛口径": dataset["profile_id"],
                "试跑回执ID": receipt["receipt_id"],
            }
            rows.append(row)
            segments.append(row)
        return segments

    def list_candidate_dataset(self, page_uri: str) -> dict[str, Any]:
        """按页面列出各正文版本的模板候选依据，并给出当前可采集资格。

        旧版本只读保留：规则调整后重算会追加新版本，不覆盖旧样本的判定依据。
        """
        rows = sorted(
            (row for row in store.rows("content_classifier")
             if row.get("页面URI") == page_uri),
            key=lambda row: int(row.get("正文版本", 0) or 0),
        )
        versions = []
        for row in rows:
            versions.append({
                "entry_id": row.get("id"),
                "body_version": row.get("正文版本"),
                "body_hash": row.get("正文哈希"),
                "body_status": row.get("正文状态"),
                "profile_id": row.get("门槛口径"),
                "primary_category": row.get("一级分类"),
                "confidence": row.get("置信度"),
                "candidate_ids": row.get("模板候选ID"),
                "adopted_template": row.get("采纳模板"),
                "trial_status": row.get("试跑结果"),
                "receipt_id": row.get("试跑回执ID"),
                "evidence_trace": row.get("证据回查"),
                "review_status": row.get("status"),
                "collectable": (
                    row.get("试跑结果") == "已采纳"
                    and bool(row.get("正文哈希"))
                    and row.get("采纳模板") not in (None, "", "—")
                ),
            })
        latest = versions[-1] if versions else None
        return {
            "ok": bool(versions),
            "page_uri": page_uri,
            "version_count": len(versions),
            "latest": latest,
            "versions": versions,
            "readonly_history": versions[:-1],
        }

    def list_template_candidates(
        self,
        *,
        page_uri: str = "",
        profile_id: str | None = None,
        qualified_only: bool = False,
    ) -> list[dict[str, Any]]:
        """分类审核清单里已经落库的模板候选依据数据集（按正文版本逐条保留）。"""
        rows = []
        for row in store.rows("content_classifier"):
            if page_uri and row.get("页面URI") != page_uri:
                continue
            if profile_id and row.get("门槛口径") != profile_id:
                continue
            if qualified_only and str(row.get("模板候选ID", "—")) in ("", "—"):
                continue
            rows.append({
                "entry_id": row.get("id"),
                "page_uri": row.get("页面URI"),
                "body_version": row.get("正文版本"),
                "body_hash": row.get("正文哈希"),
                "body_status": row.get("正文状态"),
                "profile_id": row.get("门槛口径"),
                "primary_category": row.get("一级分类"),
                "secondary_category": row.get("二级分类"),
                "candidate_ids": row.get("模板候选ID"),
                "adopted_template": row.get("采纳模板"),
                "trial_status": row.get("试跑结果"),
                "receipt_id": row.get("试跑回执ID"),
                "evidence_trace": row.get("证据回查"),
                "review_status": row.get("status"),
            })
        return sorted(rows, key=lambda item: (str(item["page_uri"]), int(item["body_version"] or 0)))

    def get_collectable_templates(self) -> dict[str, Any]:
        """可采集模板集合：只收已采纳且正文通过证据门槛的候选。

        缺正文、正文不足或低于门槛的历史记录即使曾经存在，也不会被收进来。
        """
        collectable = []
        blocked = []
        for row in store.rows(self.MODULE):
            if not row.get("来源候选ID"):
                continue
            item = {
                "template_id": row.get("id"),
                "template_name": row.get("模板名称"),
                "status": row.get("status"),
                "candidate_id": row.get("来源候选ID"),
                "body_hash": row.get("正文哈希"),
                "profile_id": row.get("门槛口径"),
                "evidence_score": row.get("证据得分"),
                "receipt_id": row.get("试跑回执ID"),
            }
            if row.get("正文哈希") and row.get("status") in ("草稿", "已验证", "已发布"):
                collectable.append(item)
            else:
                item["reason"] = "缺少正文版本或模板已失效，不得进入可采集模板集合"
                blocked.append(item)
        return {
            "ok": True,
            "collectable": collectable,
            "blocked": blocked,
            "collectable_count": len(collectable),
        }

    def get_trial_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        """按回执 ID 回查试跑回执，包含当期未采纳证据的正文片段。"""
        for row in store.rows("content_classifier"):
            if row.get("试跑回执ID") != receipt_id:
                continue
            return {
                "receipt_id": receipt_id,
                "page_uri": row.get("页面URI"),
                "body_version": row.get("正文版本"),
                "body_hash": row.get("正文哈希"),
                "profile_id": row.get("门槛口径"),
                "classification": {
                    "primary_category": row.get("一级分类"),
                    "secondary_category": row.get("二级分类"),
                    "confidence": row.get("置信度"),
                    "basis": row.get("分类依据"),
                },
                "adopted_template": row.get("采纳模板"),
                "trial_status": row.get("试跑结果"),
                "snippet_index": row.get("正文出处"),
                "snippets": row.get("正文片段"),
                "evidence_trace": json.loads(row.get("正文证据轨迹") or "[]"),
                "review_status": row.get("status"),
            }
        return None

    def resolve_conflict_status(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """把回执里的冲突处理结果压缩成可读结论，供前端和复核人查看。"""
        evidence = receipt.get("evidence") or []
        accepted = [item for item in evidence if item.get("accepted")]
        pending = [item for item in evidence if not item.get("accepted") and item.get("qualified")]
        rejected = [item for item in evidence if not item.get("qualified")]
        return {
            "receipt_id": receipt.get("receipt_id"),
            "conflict": len(evidence) > 1 and len([i for i in evidence if i.get("qualified")]) > 1,
            "adopted_template": receipt.get("adopted_template"),
            "adopt_reason": receipt.get("adopt_reason"),
            "conflict_rule": receipt.get("conflict_rule"),
            "accepted": [item["template_name"] for item in accepted],
            "pending_review": [item["template_name"] for item in pending],
            "rejected": [
                {"template_name": item["template_name"], "reason": item["gate_reason"]}
                for item in rejected
            ],
            "decision_notes": receipt.get("decision_notes") or [],
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["template_coverage"] = self.compute_template_coverage()
        return base