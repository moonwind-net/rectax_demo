<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api } from "../services/api";

interface Row {
  id: number;
  original_filename: string;
  document_status: string;
  merchant_name?: string;
  total_amount?: number;
  confidence?: number;
  stage_started_at?: string | null;
  stage_finished_at?: string | null;
  stage_duration_seconds?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  retry_attempts?: number | null;
  failure_reason?: string | null;
  can_retry?: boolean;
  uploader_id?: number | null;
  uploader_name?: string | null;
  uploader_email?: string | null;
}

interface UploaderMonthlyStat {
  uploader_name: string;
  uploader_id: number | null;
  month: string;
  total_count: number;
  success_count: number;
  success_rate: number;
  anomaly_count: number;
  anomaly_rate: number;
  avg_duration_seconds: number | null;
}

const rows = ref<Row[]>([]);
const selectedStatus = ref("all");
const uploaderKeyword = ref("");
const selectedAggregateMonth = ref("all");
const actionMessage = ref("");
const defaultFailedOnly = ref(false);
let pollTimer: number | null = null;

const FILTER_STORAGE_KEY = "rectax_documents_status_filter";
const DEFAULT_FAILED_STORAGE_KEY = "rectax_documents_default_failed_only";

const statusLabelMap: Record<string, string> = {
  uploaded: "アップロード済み",
  queued: "キュー待機中",
  ocr_processing: "OCR解析中",
  classifying: "分類中",
  review_required: "要レビュー",
  classified: "完了（承認済み）",
  approved: "完了（承認済み）",
  failed: "失敗",
};

function getStatusLabel(status: string) {
  return statusLabelMap[status] ?? status;
}

function getStatusClass(status: string) {
  return `status-pill status-pill--${status}`;
}

const statusOptions = computed(() => [
  { value: "all", label: "すべて" },
  ...Object.entries(statusLabelMap)
    .filter(([value]) => value !== "approved")
    .map(([value, label]) => ({ value, label })),
]);

const statusStats = computed(() => {
  const counts: Record<string, number> = {};
  for (const row of rows.value) {
    counts[row.document_status] = (counts[row.document_status] ?? 0) + 1;
  }
  return {
    total: rows.value.length,
    failed: counts.failed ?? 0,
    queued: counts.queued ?? 0,
    processing: (counts.ocr_processing ?? 0) + (counts.classifying ?? 0),
    review_required: counts.review_required ?? 0,
    completed: (counts.classified ?? 0) + (counts.approved ?? 0),
  };
});

const filteredRows = computed(() => {
  const statusFiltered = selectedStatus.value === "all"
    ? rows.value
    : rows.value.filter((row) => {
        if (selectedStatus.value === "classified") {
          return row.document_status === "classified" || row.document_status === "approved";
        }
        return row.document_status === selectedStatus.value;
      });

  const keyword = uploaderKeyword.value.trim().toLowerCase();
  if (!keyword) return statusFiltered;

  return statusFiltered.filter((row) => {
    const name = (row.uploader_name || "").toLowerCase();
    const email = (row.uploader_email || "").toLowerCase();
    const id = row.uploader_id != null ? String(row.uploader_id) : "";
    return name.includes(keyword) || email.includes(keyword) || id.includes(keyword);
  });
});

watch(selectedStatus, (value) => {
  localStorage.setItem(FILTER_STORAGE_KEY, value);
});

watch(defaultFailedOnly, (value) => {
  localStorage.setItem(DEFAULT_FAILED_STORAGE_KEY, value ? "1" : "0");
});

function formatDateTime(iso?: string | null) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatDuration(seconds?: number | null) {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function toMonthKey(row: Row) {
  const iso = row.created_at || row.updated_at;
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

function uploaderLabel(row: Row) {
  if (row.uploader_name && row.uploader_email) return `${row.uploader_name} <${row.uploader_email}>`;
  if (row.uploader_name) return row.uploader_name;
  if (row.uploader_email) return row.uploader_email;
  if (row.uploader_id != null) return `ID:${row.uploader_id}`;
  return "未設定";
}

function applyUploaderIdFilter(uploaderId?: number | null) {
  if (uploaderId == null) return;
  uploaderKeyword.value = String(uploaderId);
}

function toPercent(value: number, total: number) {
  if (!total) return 0;
  return Math.round((value / total) * 1000) / 10;
}

const aggregateMonths = computed(() => {
  const set = new Set<string>();
  for (const row of rows.value) {
    const month = toMonthKey(row);
    if (month !== "unknown") set.add(month);
  }
  return Array.from(set).sort((a, b) => b.localeCompare(a));
});

const uploaderMonthlyStats = computed<UploaderMonthlyStat[]>(() => {
  const buckets = new Map<string, UploaderMonthlyStat & { duration_count: number; duration_sum: number }>();

  for (const row of rows.value) {
    const month = toMonthKey(row);
    if (selectedAggregateMonth.value !== "all" && month !== selectedAggregateMonth.value) continue;

    const label = uploaderLabel(row);
    const uploaderKey = row.uploader_id != null ? `uid:${row.uploader_id}` : `name:${label}`;
    const key = `${uploaderKey}::${month}`;
    if (!buckets.has(key)) {
      buckets.set(key, {
        uploader_name: label,
        uploader_id: row.uploader_id ?? null,
        month,
        total_count: 0,
        success_count: 0,
        success_rate: 0,
        anomaly_count: 0,
        anomaly_rate: 0,
        avg_duration_seconds: null,
        duration_count: 0,
        duration_sum: 0,
      });
    }

    const bucket = buckets.get(key)!;
    bucket.total_count += 1;
    if (row.document_status === "classified" || row.document_status === "approved") {
      bucket.success_count += 1;
    }
    if (row.document_status === "failed" || row.document_status === "review_required") {
      bucket.anomaly_count += 1;
    }
    if (typeof row.stage_duration_seconds === "number" && row.stage_duration_seconds >= 0) {
      bucket.duration_sum += row.stage_duration_seconds;
      bucket.duration_count += 1;
    }
  }

  const stats = Array.from(buckets.values()).map((bucket) => {
    const success_rate = toPercent(bucket.success_count, bucket.total_count);
    const anomaly_rate = toPercent(bucket.anomaly_count, bucket.total_count);
    const avg_duration_seconds = bucket.duration_count > 0
      ? Math.round(bucket.duration_sum / bucket.duration_count)
      : null;

    return {
      uploader_name: bucket.uploader_name,
      uploader_id: bucket.uploader_id,
      month: bucket.month,
      total_count: bucket.total_count,
      success_count: bucket.success_count,
      success_rate,
      anomaly_count: bucket.anomaly_count,
      anomaly_rate,
      avg_duration_seconds,
    };
  });

  return stats.sort((a, b) => {
    if (a.month !== b.month) return b.month.localeCompare(a.month);
    if (a.total_count !== b.total_count) return b.total_count - a.total_count;
    return a.uploader_name.localeCompare(b.uploader_name);
  });
});

function exportUploaderMonthlyCsv() {
  const header = [
    "month",
    "uploader_name",
    "uploader_id",
    "total_count",
    "success_count",
    "success_rate_percent",
    "avg_duration_seconds",
    "anomaly_count",
    "anomaly_rate_percent",
  ];

  const rowsCsv = uploaderMonthlyStats.value.map((row) => [
    row.month,
    row.uploader_name,
    row.uploader_id ?? "",
    row.total_count,
    row.success_count,
    row.success_rate.toFixed(1),
    row.avg_duration_seconds ?? "",
    row.anomaly_count,
    row.anomaly_rate.toFixed(1),
  ]);

  const escapeCsv = (value: string | number) => {
    const text = String(value);
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };

  const csvContent = [header, ...rowsCsv]
    .map((cols) => cols.map((c) => escapeCsv(c)).join(","))
    .join("\n");

  const blob = new Blob([`\uFEFF${csvContent}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const monthSuffix = selectedAggregateMonth.value === "all" ? "all" : selectedAggregateMonth.value;
  a.href = url;
  a.download = `uploader_monthly_stats_${monthSuffix}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function retryProcessing(row: Row) {
  actionMessage.value = "";
  await api.post(`/documents/${row.id}/retry-processing`);
  actionMessage.value = `ID ${row.id} の再処理を受け付けました。`;
  await load();
}

async function retryFailedAll() {
  actionMessage.value = "";
  const { data } = await api.post("/documents/retry-failed");
  const count = typeof data?.retried_count === "number" ? data.retried_count : 0;
  actionMessage.value = count > 0 ? `${count} 件の失敗タスクを再投入しました。` : "再投入対象の失敗タスクはありません。";
  await load();
}

async function rerunOcr(row: Row) {
  actionMessage.value = "";
  await api.post(`/documents/${row.id}/re-ocr`);
  actionMessage.value = `ID ${row.id} を OCR 再解析キューに追加しました。`;
  await load();
}

async function rerunOcrBulk() {
  const ids = filteredRows.value.map((r) => r.id);
  if (ids.length === 0) {
    actionMessage.value = "絞り込み結果が 0 件です。";
    return;
  }
  if (!window.confirm(`現在の絞り込み結果 ${ids.length} 件を OCR 再解析キューに追加しますか？`)) return;
  actionMessage.value = "";
  const { data } = await api.post("/documents/re-ocr-bulk", { document_ids: ids });
  const queued: number = data?.queued_count ?? 0;
  const skipped: number = data?.skipped_count ?? 0;
  actionMessage.value = `${queued} 件をキューに追加しました。${skipped > 0 ? ` (処理中のためスキップ: ${skipped} 件)` : ""}`;
  await load();
}

async function load() {
  const { data } = await api.get("/documents");
  rows.value = data;
}

onMounted(() => {
  defaultFailedOnly.value = localStorage.getItem(DEFAULT_FAILED_STORAGE_KEY) === "1";
  const savedFilter = localStorage.getItem(FILTER_STORAGE_KEY);
  if (savedFilter) {
    selectedStatus.value = savedFilter === "approved" ? "classified" : savedFilter;
  } else if (defaultFailedOnly.value) {
    selectedStatus.value = "failed";
  }

  load();
  pollTimer = window.setInterval(load, 5000);
});

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
});
</script>

<template>
  <section class="card">
    <div class="toolbar">
      <h2>OCR結果一覧</h2>
      <button class="secondary" @click="load">再読み込み</button>
      <button class="secondary" @click="selectedStatus = 'failed'">失敗のみ表示</button>
      <button class="secondary" @click="selectedStatus = 'all'">絞り込み解除</button>
      <button class="secondary" @click="retryFailedAll">失敗タスクを一括再処理</button>
      <button class="secondary" @click="rerunOcrBulk">絞り込み結果を一括OCR再解析</button>
      <div class="filter-field">
        <label for="status-filter">ステータス絞り込み</label>
        <select id="status-filter" v-model="selectedStatus">
          <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
      </div>
      <label class="switch-inline">
        <input type="checkbox" v-model="defaultFailedOnly" />
        初期表示を「失敗のみ」にする
      </label>
      <div class="filter-field">
        <label for="uploader-filter">担当者検索</label>
        <input id="uploader-filter" v-model="uploaderKeyword" type="text" placeholder="担当者名 / 邮箱 / ID" />
      </div>
      <div class="filter-field">
        <label for="aggregate-month">集計月</label>
        <select id="aggregate-month" v-model="selectedAggregateMonth">
          <option value="all">全期間</option>
          <option v-for="month in aggregateMonths" :key="month" :value="month">{{ month }}</option>
        </select>
      </div>
      <button class="secondary" @click="exportUploaderMonthlyCsv">担当者集計CSV出力</button>
    </div>
    <p class="filter-hint">絞り込み条件は自動保存され、次回アクセス時に復元されます。</p>

    <div class="status-stats" aria-label="status-stats">
      <div class="stat-box">
        <p class="stat-label">総件数</p>
        <p class="stat-value">{{ statusStats.total }}</p>
      </div>
      <div class="stat-box">
        <p class="stat-label">処理中</p>
        <p class="stat-value">{{ statusStats.processing }}</p>
      </div>
      <div class="stat-box">
        <p class="stat-label">要レビュー</p>
        <p class="stat-value">{{ statusStats.review_required }}</p>
      </div>
      <div class="stat-box">
        <p class="stat-label">完了（承認済み）</p>
        <p class="stat-value">{{ statusStats.completed }}</p>
      </div>
      <div class="stat-box stat-box--danger">
        <p class="stat-label">失敗</p>
        <p class="stat-value">{{ statusStats.failed }}</p>
      </div>
      <div class="stat-box">
        <p class="stat-label">待機中</p>
        <p class="stat-value">{{ statusStats.queued }}</p>
      </div>
    </div>

    <p v-if="actionMessage" class="action-message">{{ actionMessage }}</p>

    <section class="aggregate-card">
      <div class="aggregate-head">
        <h3>担当者別 月次集計</h3>
        <p class="aggregate-caption">成功率 = 完了（承認済み） / 件数、異常率 = (failed + review_required) / 件数</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>月</th>
              <th>担当者</th>
              <th>件数</th>
              <th>成功件数</th>
              <th>成功率</th>
              <th>平均処理時間</th>
              <th>異常件数</th>
              <th>異常率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in uploaderMonthlyStats" :key="`${row.month}-${row.uploader_name}-${row.uploader_id ?? 'x'}`">
              <td>{{ row.month }}</td>
              <td>{{ row.uploader_name }}</td>
              <td>{{ row.total_count }}</td>
              <td>{{ row.success_count }}</td>
              <td>{{ row.success_rate.toFixed(1) }}%</td>
              <td>{{ formatDuration(row.avg_duration_seconds) }}</td>
              <td>{{ row.anomaly_count }}</td>
              <td>{{ row.anomaly_rate.toFixed(1) }}%</td>
            </tr>
            <tr v-if="uploaderMonthlyStats.length === 0">
              <td colspan="8" class="empty-cell">集計対象データがありません。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>ファイル名</th>
            <th>ステータス</th>
            <th>店舗名</th>
            <th>金額</th>
            <th>信頼度</th>
            <th>担当者ID</th>
            <th>担当者</th>
            <th>開始時間</th>
            <th>処理時間</th>
            <th>最終更新</th>
            <th>再試行回数</th>
            <th>失敗原因</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.id">
            <td>{{ row.id }}</td>
            <td>
              <router-link :to="`/documents/${row.id}`">{{ row.original_filename }}</router-link>
            </td>
            <td><span :class="getStatusClass(row.document_status)">{{ getStatusLabel(row.document_status) }}</span></td>
            <td>{{ row.merchant_name || "-" }}</td>
            <td>{{ row.total_amount ?? "-" }}</td>
            <td>{{ row.confidence ?? "-" }}</td>
            <td>
              <button
                v-if="row.uploader_id != null"
                type="button"
                class="id-filter-btn"
                @click="applyUploaderIdFilter(row.uploader_id)"
              >
                {{ row.uploader_id }}
              </button>
              <span v-else>-</span>
            </td>
            <td>{{ uploaderLabel(row) }}</td>
            <td>{{ formatDateTime(row.stage_started_at) }}</td>
            <td>{{ formatDuration(row.stage_duration_seconds) }}</td>
            <td>{{ formatDateTime(row.updated_at) }}</td>
            <td>{{ row.retry_attempts ?? 0 }}</td>
            <td class="failure-cell">{{ row.failure_reason || "-" }}</td>
            <td>
              <button v-if="row.can_retry" class="secondary" @click="retryProcessing(row)">再試行</button>
              <button
                v-else-if="row.document_status === 'review_required'"
                class="secondary"
                @click="rerunOcr(row)"
              >
                OCR再解析
              </button>
              <span v-else>-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="mobile-card-list">
          <article class="mobile-doc-card" v-for="row in filteredRows" :key="`mobile-${row.id}`">
        <header class="mobile-doc-header">
          <p class="mobile-doc-title"><router-link :to="`/documents/${row.id}`">{{ row.original_filename }}</router-link></p>
          <span :class="getStatusClass(row.document_status)">{{ getStatusLabel(row.document_status) }}</span>
        </header>
        <div class="mobile-doc-grid">
          <p><strong>ID</strong> {{ row.id }}</p>
          <p><strong>店舗名</strong> {{ row.merchant_name || "-" }}</p>
          <p><strong>金額</strong> {{ row.total_amount ?? "-" }}</p>
          <p><strong>信頼度</strong> {{ row.confidence ?? "-" }}</p>
          <p>
            <strong>担当者ID</strong>
            <button
              v-if="row.uploader_id != null"
              type="button"
              class="id-filter-btn"
              @click="applyUploaderIdFilter(row.uploader_id)"
            >
              {{ row.uploader_id }}
            </button>
            <span v-else>-</span>
          </p>
          <p><strong>担当者</strong> {{ uploaderLabel(row) }}</p>
          <p><strong>開始時間</strong> {{ formatDateTime(row.stage_started_at) }}</p>
          <p><strong>処理時間</strong> {{ formatDuration(row.stage_duration_seconds) }}</p>
          <p><strong>最終更新</strong> {{ formatDateTime(row.updated_at) }}</p>
          <p><strong>再試行回数</strong> {{ row.retry_attempts ?? 0 }}</p>
          <p class="mobile-failure"><strong>失敗原因</strong> {{ row.failure_reason || "-" }}</p>
        </div>
        <button v-if="row.document_status === 'review_required'" class="secondary" @click="rerunOcr(row)">OCR再解析</button>
        <button v-if="row.can_retry" class="secondary" @click="retryProcessing(row)">再試行</button>
      </article>
    </div>
  </section>
</template>

<style scoped>
.filter-field {
  min-width: 180px;
}

.switch-inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #3e536d;
}

.switch-inline input {
  width: auto;
}

.filter-hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: #61748d;
}

.filter-field label {
  display: block;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 4px;
  color: #4d5d72;
}

.status-stats {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.stat-box {
  border: 1px solid #d5deea;
  border-radius: 10px;
  background: #f8fbff;
  padding: 8px 10px;
}

.stat-box--danger {
  border-color: #efc1c1;
  background: #fff3f3;
}

.stat-label {
  margin: 0;
  font-size: 11px;
  color: #5c6e85;
}

.stat-value {
  margin: 4px 0 0;
  font-size: 18px;
  font-weight: 700;
  color: #20344f;
}

.action-message {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef6ff;
  border: 1px solid #c8dbf3;
  color: #214b78;
  font-size: 13px;
}

.aggregate-card {
  border: 1px solid #d8e1ee;
  border-radius: 12px;
  background: #f8fbff;
  padding: 12px;
  margin-bottom: 14px;
}

.aggregate-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.aggregate-head h3 {
  margin: 0;
  font-size: 15px;
  color: #213a59;
}

.aggregate-caption {
  margin: 0;
  font-size: 12px;
  color: #607892;
}

.empty-cell {
  text-align: center;
  color: #607892;
}

.status-pill {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.status-pill--uploaded,
.status-pill--queued {
  background: #e8eef9;
  color: #27476f;
}

.status-pill--ocr_processing,
.status-pill--classifying {
  background: #fff4df;
  color: #8b5200;
}

.status-pill--review_required {
  background: #ffe7e1;
  color: #a33420;
}

.status-pill--classified,
.status-pill--approved {
  background: #e2f4e8;
  color: #1f6b3a;
}

.status-pill--failed {
  background: #fce3e3;
  color: #9b1f1f;
}

.failure-cell {
  max-width: 300px;
  white-space: normal;
  color: #8f2a2a;
}

.id-filter-btn {
  width: auto;
  min-width: 0;
  border: 1px solid #b8c9df;
  background: #eef4fb;
  color: #244a72;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.5;
}

.id-filter-btn:hover {
  border-color: #8ba9cb;
  background: #dfeaf8;
}

.mobile-card-list {
  display: none;
}

@media (max-width: 900px) {
  .switch-inline {
    width: 100%;
    font-size: 12px;
  }

  .status-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 7px;
  }

  .table-wrap {
    display: none;
  }

  .mobile-card-list {
    display: grid;
    gap: 10px;
  }

  .aggregate-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .mobile-doc-card {
    border: 1px solid #d5deea;
    background: #fbfdff;
    border-radius: 12px;
    padding: 10px;
  }

  .mobile-doc-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }

  .mobile-doc-title {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: #243b58;
    line-height: 1.35;
    word-break: break-all;
  }

  .mobile-doc-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 4px;
    margin-bottom: 8px;
  }

  .mobile-doc-grid p {
    margin: 0;
    font-size: 12px;
    color: #33495f;
    line-height: 1.45;
  }

  .mobile-doc-grid strong {
    margin-right: 4px;
    color: #1f3551;
  }

  .mobile-failure {
    color: #8f2a2a !important;
  }

  h2 {
    margin-bottom: 0;
  }
}
</style>