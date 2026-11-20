import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type EvidenceProfile = {
  profile_id: string
  label: string
  min_body_chars: number
  categories: {
    category: string
    template_name: string
    threshold: number
    min_signals: number
    signals: { id: string; label: string; weight: number }[]
  }[]
}

export type EvidenceCandidate = {
  candidate_id: string
  category: string
  template_name: string
  evidence_score: number
  threshold: number
  min_signals: number
  matched_signal_count: number
  matched_signals: {
    signal_id: string
    signal_label: string
    weight: number
    quote: string
    quote_start: number
    quote_end: number
  }[]
  gate: string
  gate_reason: string
  qualified: boolean
  collectable: boolean
  block_reason: string
}

export type TrialField = {
  field: string
  value: string | null
  status: string
  selector: string
  quote: string | null
  quote_start: number | null
  quote_end: number | null
}

export type TrialSample = {
  candidate_id: string
  template_name: string
  category: string
  evidence_score: number
  threshold: number
  field_count: number
  hit_count: number
  field_coverage: number
  min_coverage: number
  trial_status: string
  fields: TrialField[]
}

export type TrialReceipt = {
  receipt_id: string
  profile_id: string
  body_hash: string
  body_status: string
  adopted_template: string
  adopted_category: string
  adopt_reason: string
  conflict_rule: string
  review_status: string
  decision_notes: string[]
  evidence: {
    category: string
    template_name: string
    evidence_score: number
    threshold: number
    gate_note: string
    qualified: boolean
    accepted: boolean
    trial_status: string
    review_status: string
    matched_signals: { signal_id: string; quote: string; quote_start: number }[]
  }[]
  evidence_trace: {
    category: string
    signal_id: string
    quote: string
    quote_start: number
    quote_end: number
    accepted: boolean
  }[]
  rejected_evidence: {
    candidate_id: string
    category: string
    gate_reason: string
    matched_signals: { quote: string; quote_start: number }[]
  }[]
  review_writeback?: { entry_id: number; body_version: number; template_id: number | null }
}

export type CandidateDataset = {
  ok: boolean
  page_uri: string
  profile_id: string
  body_hash: string
  body_status: string
  body_reason: string
  body_valid: boolean
  char_count: number
  classification: {
    primary_category: string
    secondary_category: string
    confidence: number
    qualified_categories: string[]
    basis: string
  }
  candidates: EvidenceCandidate[]
  qualified_candidate_ids: string[]
}

export type TrialResult = {
  ok: boolean
  message?: string
  result?: string
  dataset: CandidateDataset
  samples: TrialSample[]
  adopted_candidate: (TrialSample & { adopt_reason?: string }) | null
  trial_receipt: TrialReceipt
  review_entry: {
    entry_id: number
    body_version: number
    template_id: number | null
    reused: boolean
  } | null
}

export type EvidenceSamples = {
  page_uri: string
  profile_id: string
  profiles: EvidenceProfile[]
  bodies: { key: string; title: string; expected_categories: string; body: string }[]
}

export async function loadEvidenceSamples(): Promise<EvidenceSamples> {
  const response = await request('/api/evidence/samples')
  if (!response.ok) throw new Error('正文样本读取失败')
  return await response.json()
}

async function postEvidence<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return (await response.json()) as T
}

export function classifyByEvidence(payload: {
  page_uri: string
  body: string
  profile_id?: string
}): Promise<Record<string, unknown>> {
  return postEvidence('/api/evidence/classify', payload)
}

export function buildCandidateDataset(payload: {
  page_uri: string
  body: string
  profile_id?: string
}): Promise<CandidateDataset> {
  return postEvidence('/api/evidence/datasets', { ...payload, source: 'workbench' })
}

export function runEvidenceTrial(payload: {
  page_uri: string
  body: string
  profile_id?: string
  sync_review?: boolean
}): Promise<TrialResult> {
  return postEvidence('/api/evidence/trials', { ...payload, source: 'workbench' })
}

export type CollectableTemplate = {
  template_id: number
  template_name: string
  status: string
  candidate_id: string
  body_hash: string
  profile_id: string
  evidence_score: string
  receipt_id: string
}

export async function loadCollectableTemplates(): Promise<{
  collectable: CollectableTemplate[]
  blocked: { template_name: string; reason: string }[]
  collectable_count: number
}> {
  const response = await request('/api/evidence/collectable')
  if (!response.ok) throw new Error('可采集模板读取失败')
  return await response.json()
}

export async function loadPageDataset(pageUri: string): Promise<{
  version_count: number
  versions: Record<string, unknown>[]
}> {
  const response = await request(`/api/evidence/dataset?page_uri=${encodeURIComponent(pageUri)}`)
  if (!response.ok) throw new Error('候选依据版本读取失败')
  return await response.json()
}
