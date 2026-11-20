<template>
  <section class="evidence-panel" data-testid="evidence-panel">
    <header class="evidence-head">
      <div>
        <h3>正文证据校验与模板试跑</h3>
        <p class="page-desc">
          分类改为按正文证据打分；只有通过证据门槛的候选才会生成试跑字段，缺正文或低于门槛的候选不进入可采集模板集合。
        </p>
      </div>
      <button class="btn" type="button" data-testid="evidence-refresh" @click="loadCollectable">
        刷新可采集模板
      </button>
    </header>

    <form class="evidence-form" @submit.prevent="runTrial">
      <label class="evidence-field">
        <span>页面 URI</span>
        <input v-model="pageUri" data-testid="evidence-page-uri" placeholder="https://..." />
      </label>
      <label class="evidence-field">
        <span>门槛口径</span>
        <select v-model="profileId" data-testid="evidence-profile">
          <option v-for="profile in profiles" :key="profile.profile_id" :value="profile.profile_id">
            {{ profile.label }}
          </option>
        </select>
      </label>
      <label class="evidence-field wide">
        <span>页面正文（可粘贴采集到的正文）</span>
        <textarea
          v-model="body"
          rows="6"
          data-testid="evidence-body"
          placeholder="粘贴正文后先看候选依据，再试跑模板字段"
        />
      </label>
      <div class="evidence-actions">
        <button
          v-for="sample in demoBodies"
          :key="sample.key"
          class="btn ghost"
          type="button"
          :data-testid="`evidence-sample-${sample.key}`"
          @click="applySample(sample)"
        >
          {{ sample.title }}
        </button>
        <span class="evidence-spacer" />
        <button class="btn" type="button" data-testid="evidence-dataset" @click="runDataset">
          生成候选依据
        </button>
        <button class="btn primary" type="submit" data-testid="evidence-trial" :disabled="busy">
          {{ busy ? '试跑中…' : '试跑并回写审核清单' }}
        </button>
      </div>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="evidence-error">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="evidence-notice" data-testid="evidence-notice">{{ noticeMessage }}</p>

    <div v-if="dataset" class="evidence-grid">
      <article class="evidence-card" data-testid="dataset-card">
        <h4>模板候选依据数据集</h4>
        <dl class="evidence-meta">
          <dt>页面</dt><dd>{{ dataset.page_uri }}</dd>
          <dt>正文状态</dt><dd>{{ dataset.body_status }}（{{ dataset.char_count }} 字符）</dd>
          <dt>正文版本</dt><dd>{{ dataset.body_hash || '—' }}</dd>
          <dt>门槛口径</dt><dd>{{ dataset.profile_id }}</dd>
          <dt>分类结论</dt>
          <dd>
            {{ dataset.classification.primary_category }} / {{ dataset.classification.secondary_category }}
            （{{ Number(dataset.classification.confidence).toFixed(2) }}）
          </dd>
          <dt>分类依据</dt><dd>{{ dataset.classification.basis }}</dd>
        </dl>
        <table class="data-table">
          <thead>
            <tr><th>候选类别</th><th>模板</th><th>证据得分</th><th>门槛</th><th>命中信号</th><th>门槛结论</th><th>可采集</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="candidate in dataset.candidates"
              :key="candidate.candidate_id"
              :data-testid="`candidate-${candidate.category}`"
            >
              <td>{{ candidate.category }}</td>
              <td>{{ candidate.template_name }}</td>
              <td>{{ Number(candidate.evidence_score).toFixed(2) }}</td>
              <td>{{ Number(candidate.threshold).toFixed(2) }}</td>
              <td>{{ candidate.matched_signal_count }} / {{ candidate.min_signals }}</td>
              <td :title="candidate.gate_reason">{{ candidate.gate }}</td>
              <td>{{ candidate.collectable ? '是' : '否' }}</td>
            </tr>
            <tr v-if="!dataset.candidates.length">
              <td colspan="7" class="empty-state">{{ dataset.body_reason }}</td>
            </tr>
          </tbody>
        </table>
      </article>

      <article v-if="receipt" class="evidence-card" data-testid="receipt-card">
        <h4>试跑回执 {{ receipt.receipt_id }}</h4>
        <dl class="evidence-meta">
          <dt>采纳模板</dt><dd>{{ receipt.adopted_template || '无（待复核）' }}</dd>
          <dt>裁定规则</dt><dd>{{ receipt.conflict_rule || '无冲突' }}</dd>
          <dt>裁定说明</dt><dd>{{ receipt.adopt_reason || '—' }}</dd>
          <dt>审核状态</dt><dd>{{ receipt.review_status }}</dd>
        </dl>
        <ul class="evidence-notes">
          <li v-for="(note, index) in receipt.decision_notes" :key="index">{{ note }}</li>
        </ul>
        <table class="data-table">
          <thead>
            <tr><th>模板</th><th>结论</th><th>试跑</th><th>审核</th><th>片段</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="item in receipt.evidence"
              :key="item.category"
              :data-testid="`receipt-${item.category}`"
            >
              <td>{{ item.template_name }}</td>
              <td :title="item.gate_note">{{ item.qualified ? '通过门槛' : '低于门槛' }}</td>
              <td>{{ item.trial_status }}</td>
              <td>{{ item.review_status }}</td>
              <td class="evidence-quote">
                {{ item.matched_signals[0]?.quote || item.gate_note }}
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="receipt.rejected_evidence.length" class="evidence-rejected">
          <h5>当期未采纳的证据（可按片段回查）</h5>
          <p
            v-for="item in receipt.rejected_evidence"
            :key="item.candidate_id"
            :data-testid="`rejected-${item.category}`"
          >
            <strong>{{ item.category }}</strong>：{{ item.gate_reason }}
            <em v-if="item.matched_signals.length">「{{ item.matched_signals[0].quote }}」</em>
          </p>
        </div>
      </article>
    </div>

    <article v-if="trialSamples.length" class="evidence-card" data-testid="samples-card">
      <h4>各候选试跑字段（带正文出处）</h4>
      <div v-for="sample in trialSamples" :key="sample.candidate_id" class="sample-block" :data-testid="`sample-${sample.category}`">
        <p class="sample-head">
          {{ sample.template_name }} · 证据 {{ Number(sample.evidence_score).toFixed(2) }} ·
          字段覆盖率 {{ Number(sample.field_coverage).toFixed(2) }} / {{ Number(sample.min_coverage).toFixed(2) }} ·
          {{ sample.trial_status }}
        </p>
        <table class="data-table">
          <thead><tr><th>字段</th><th>选择器</th><th>试跑值</th><th>正文片段</th><th>位置</th></tr></thead>
          <tbody>
            <tr v-for="field in sample.fields" :key="field.field">
              <td>{{ field.field }}</td>
              <td><code>{{ field.selector }}</code></td>
              <td>{{ field.value ?? '—' }}</td>
              <td class="evidence-quote">{{ field.quote || '—' }}</td>
              <td>{{ field.quote_start ?? '—' }}-{{ field.quote_end ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <article class="evidence-card">
      <h4>可采集模板集合（{{ collectableCount }}）</h4>
      <p class="page-desc">只包含正文通过门槛且已采纳的模板；缺正文或低于门槛的记录不会出现在这里。</p>
      <table class="data-table">
        <thead><tr><th>模板</th><th>状态</th><th>正文版本</th><th>门槛口径</th><th>回执</th></tr></thead>
        <tbody>
          <tr v-for="item in collectable" :key="item.template_id" :data-testid="`collectable-${item.template_id}`">
            <td>{{ item.template_name }}</td>
            <td>{{ item.status }}</td>
            <td>{{ item.body_hash }}</td>
            <td>{{ item.profile_id }}</td>
            <td>{{ item.receipt_id }}</td>
          </tr>
          <tr v-if="!collectable.length">
            <td colspan="5" class="empty-state">还没有符合条件的可采集模板</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  loadCollectableTemplates,
  loadEvidenceSamples,
  buildCandidateDataset,
  runEvidenceTrial,
} from '@/api/backend-service'
import type {
  CandidateDataset,
  CollectableTemplate,
  EvidenceProfile,
  TrialReceipt,
  TrialSample,
} from '@/api/backend-service'

const pageUri = ref('')
const profileId = ref('')
const body = ref('')
const profiles = ref<EvidenceProfile[]>([])
const demoBodies = ref<{ key: string; title: string; expected_categories: string; body: string }[]>([])
const dataset = ref<CandidateDataset | null>(null)
const receipt = ref<TrialReceipt | null>(null)
const trialSamples = ref<TrialSample[]>([])
const collectable = ref<CollectableTemplate[]>([])
const collectableCount = ref(0)
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')

function applySample(sample: { key: string; body: string }) {
  body.value = sample.body
  errorMessage.value = ''
  noticeMessage.value = `已载入「${demoBodies.value.find((item) => item.key === sample.key)?.title}」`
}

async function loadCollectable() {
  try {
    const payload = await loadCollectableTemplates()
    collectable.value = payload.collectable
    collectableCount.value = payload.collectable_count
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '可采集模板读取失败'
  }
}

async function runDataset() {
  errorMessage.value = ''
  busy.value = true
  try {
    const result = await buildCandidateDataset({
      page_uri: pageUri.value,
      body: body.value,
      profile_id: profileId.value,
    })
    dataset.value = result
    receipt.value = null
    trialSamples.value = []
    noticeMessage.value = result.ok
      ? `候选依据已生成：${result.qualified_candidate_ids.length} 个候选通过门槛`
      : result.body_reason
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '候选依据生成失败'
  } finally {
    busy.value = false
  }
}

async function runTrial() {
  errorMessage.value = ''
  noticeMessage.value = ''
  busy.value = true
  try {
    const result = await runEvidenceTrial({
      page_uri: pageUri.value,
      body: body.value,
      profile_id: profileId.value,
      sync_review: true,
    })
    dataset.value = result.dataset
    receipt.value = result.trial_receipt
    trialSamples.value = result.samples
    noticeMessage.value = result.ok
      ? `试跑完成：${result.result}，回执 ${result.trial_receipt.receipt_id} 已回写审核清单`
      : result.message ?? result.trial_receipt.decision_notes.join('；')
    await loadCollectable()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试跑失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await loadCollectable()
  try {
    const payload = await loadEvidenceSamples()
    pageUri.value = payload.page_uri
    profiles.value = payload.profiles
    profileId.value = payload.profile_id
    demoBodies.value = payload.bodies
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '正文样本读取失败'
  }
})
</script>
