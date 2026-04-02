<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../services/api";

const files = ref<File[]>([]);
const message = ref("アップロード待機中");
const uploading = ref(false);
const dragActive = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const isCoarsePointer = ref(false);
const supportsFolderSelection = ref(false);
const batchSize = ref(3);
const delaySeconds = ref(2);
const maxRetries = ref(3);
const retryBaseSeconds = ref(3);

// Runtime state
const aborted = ref(false);
const healthOk = ref(true);
const healthStatusText = ref("");
const currentPhase = ref<"idle" | "health" | "uploading" | "waiting" | "done" | "aborted" | "error">("idle");
const currentBatch = ref(0);
const totalBatches = ref(0);
const acceptedCount = ref(0);
const etaSeconds = ref<number | null>(null);
const lastFailureReason = ref("");

const phaseLabelMap: Record<string, string> = {
  idle: "待機中",
  health: "ヘルス確認中",
  uploading: "アップロード中",
  waiting: "次バッチ待機",
  done: "受理完了",
  aborted: "中断",
  error: "エラー",
};

const isMobileUploadMode = computed(() => isCoarsePointer.value);
const dropzoneTitle = computed(() =>
  isMobileUploadMode.value ? "タップしてファイルを選択" : "ここにファイルをドラッグ&ドロップ"
);
const dropzoneSubtitle = computed(() =>
  isMobileUploadMode.value
    ? "スマホではドラッグ操作に対応しない場合があります。タップして複数ファイルを選択してください。"
    : "またはクリックして複数ファイルを選択"
);
const pageIntro = computed(() =>
  isMobileUploadMode.value
    ? "スマホ向けクリックアップロードに対応。アップロード後に OCR と分類を自動実行します。"
    : "ドラッグ&ドロップ/一括アップロードに対応。アップロード後に OCR と分類を自動実行します。"
);
const phaseLabel = computed(() => phaseLabelMap[currentPhase.value] ?? "待機中");
const uploadProgressPercent = computed(() => {
  if (totalBatches.value <= 0) return 0;
  return Math.min(100, Math.round((currentBatch.value / totalBatches.value) * 100));
});
const etaLabel = computed(() => {
  if (etaSeconds.value == null) return "算出中";
  if (etaSeconds.value <= 0) return "まもなく完了";
  if (etaSeconds.value < 60) return `約 ${etaSeconds.value} 秒`;
  const m = Math.floor(etaSeconds.value / 60);
  const s = etaSeconds.value % 60;
  return `約 ${m} 分 ${s} 秒`;
});

let mediaQuery: MediaQueryList | null = null;

function refreshPointerMode() {
  isCoarsePointer.value = window.matchMedia("(pointer: coarse)").matches;
}

function updateSelectedFiles(nextFiles: File[]) {
  files.value = nextFiles;
  const count = files.value.length;
  currentPhase.value = "idle";
  currentBatch.value = 0;
  totalBatches.value = 0;
  acceptedCount.value = 0;
  etaSeconds.value = null;
  message.value = count > 0 ? `${count}件のファイルを選択しました` : "アップロード待機中";
}

function openFileDialog() {
  const input = fileInput.value as (HTMLInputElement & { showPicker?: () => void }) | null;
  if (!input) return;
  // Allow selecting the same file again by resetting the control first.
  input.value = "";
  if (typeof input.showPicker === "function") {
    try {
      input.showPicker();
      return;
    } catch {
      // Fallback to click for browsers that gate showPicker.
    }
  }
  input.click();
}

function openFolderDialog() {
  folderInput.value?.click();
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const selected = input.files ? Array.from(input.files) : [];
  updateSelectedFiles(selected);
}

function onFolderChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const selected = input.files ? Array.from(input.files) : [];
  if (selected.length === 0) {
    return;
  }
  updateSelectedFiles(selected);
}

function onDragOver(event: DragEvent) {
  event.preventDefault();
  dragActive.value = true;
}

function onDragLeave(event: DragEvent) {
  event.preventDefault();
  dragActive.value = false;
}

function onDrop(event: DragEvent) {
  event.preventDefault();
  dragActive.value = false;

  const dropped = event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : [];
  if (dropped.length === 0) {
    return;
  }
  updateSelectedFiles(dropped);
}

onMounted(() => {
  mediaQuery = window.matchMedia("(pointer: coarse)");
  refreshPointerMode();
  mediaQuery.addEventListener("change", refreshPointerMode);

  const probe = document.createElement("input") as HTMLInputElement & { webkitdirectory?: boolean };
  supportsFolderSelection.value = "webkitdirectory" in probe || "directory" in probe;
});

onUnmounted(() => {
  mediaQuery?.removeEventListener("change", refreshPointerMode);
});

// ── helpers ──────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function checkHealthOnce(): Promise<boolean> {
  try {
    // Health endpoint is exposed at /health (not /api/v1/health).
    const response = await fetch("/health", { method: "GET" });
    if (!response.ok) {
      throw new Error(`health_status_${response.status}`);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Polls /health every POLL_INTERVAL seconds until the backend responds.
 * Updates healthStatusText with a live countdown. Returns false if aborted.
 */
async function waitForHealthy(): Promise<boolean> {
  const POLL_INTERVAL = 5;
  let attempt = 0;
  // Fast first check — skip delay if already healthy
  if (await checkHealthOnce()) {
    healthOk.value = true;
    healthStatusText.value = "";
    return true;
  }
  while (!aborted.value) {
    healthOk.value = false;
    attempt += 1;
    for (let remaining = POLL_INTERVAL; remaining > 0 && !aborted.value; remaining--) {
      healthStatusText.value = `バックエンドが応答していません — ${remaining}秒後に再確認中 (${attempt}回目)`;
      await sleep(1000);
    }
    if (aborted.value) return false;
    if (await checkHealthOnce()) {
      healthOk.value = true;
      healthStatusText.value = "";
      return true;
    }
  }
  return false;
}

/**
 * Uploads one FormData batch, retrying on transient errors with exponential backoff.
 */
async function uploadBatchWithRetry(
  form: FormData,
  label: string,
): Promise<{ document_ids: number[]; duplicate_document_ids: number[] }> {
  const maxR = Math.max(0, Math.floor(maxRetries.value || 0));
  const baseMs = Math.max(1, Math.floor(retryBaseSeconds.value || 3)) * 1000;

  for (let attempt = 0; attempt <= maxR; attempt++) {
    try {
      const { data } = await api.post("/ingestion/upload", form);
      return data;
    } catch (err: any) {
      if (err?.response?.status === 413) throw err; // never retry payload-too-large
      if (aborted.value) throw Object.assign(new Error("upload_aborted"), { aborted: true });

      const detail = err?.response?.data?.detail;
      const statusCode = err?.response?.status;
      const summary = typeof detail === "string" ? detail : statusCode ? `HTTP ${statusCode}` : "通信エラー";
      lastFailureReason.value = `${label}: ${summary}`;

      if (attempt >= maxR) throw err;

      // Respect server-side Retry-After hint (rate limit response)
      const retryAfterSec = err?.response?.status === 429
        ? (parseInt(err?.response?.headers?.['retry-after'] ?? '0', 10) || 0)
        : 0;
      const backoffMs = baseMs * Math.pow(2, attempt);
      const waitMs = Math.max(backoffMs, retryAfterSec * 1000);
      const waitSec = Math.round(waitMs / 1000);
      for (let remaining = waitSec; remaining > 0 && !aborted.value; remaining--) {
        message.value = `${label} — リクエスト失敗 (${attempt + 1}/${maxR}回)、${remaining}秒後にリトライ...`;
        await sleep(1000);
      }
      if (aborted.value) throw Object.assign(new Error("upload_aborted"), { aborted: true });
    }
  }
  throw new Error("unreachable");
}

async function upload() {
  if (files.value.length === 0) {
    message.value = "先にファイルを選択してください";
    return;
  }

  const safeBatchSize = Math.max(1, Math.floor(batchSize.value || 1));
  const safeDelaySeconds = Math.max(0, Math.floor(delaySeconds.value || 0));
  const chunks: File[][] = [];
  for (let i = 0; i < files.value.length; i += safeBatchSize) {
    chunks.push(files.value.slice(i, i + safeBatchSize));
  }

  aborted.value = false;
  healthOk.value = true;
  healthStatusText.value = "";

  try {
    uploading.value = true;
    currentPhase.value = "health";
    currentBatch.value = 0;
    totalBatches.value = chunks.length;
    acceptedCount.value = 0;
    etaSeconds.value = null;
    lastFailureReason.value = "";
    const runStartedAt = Date.now();
    let totalProcessed = 0;
    const allDuplicateIds: number[] = [];

    for (let idx = 0; idx < chunks.length; idx += 1) {
      if (aborted.value) {
        message.value = `キャンセルされました。処理済み: ${totalProcessed}件`;
        break;
      }

      const label = `バッチ ${idx + 1}/${chunks.length}`;
      currentBatch.value = idx;

      // ① Health-guard: pause here until backend is healthy
      currentPhase.value = "health";
      message.value = `${label} — ヘルスチェック中...`;
      const healthy = await waitForHealthy();
      if (!healthy || aborted.value) {
        currentPhase.value = "aborted";
        message.value = `キャンセルされました。処理済み: ${totalProcessed}件`;
        break;
      }

      // ② Upload with retry
      currentPhase.value = "uploading";
      message.value = `アップロード中... ${label}`;
      const current = chunks[idx];
      const form = new FormData();
      current.forEach((f) => form.append("files", f));

      const data = await uploadBatchWithRetry(form, label);
      totalProcessed += Array.isArray(data.document_ids) ? data.document_ids.length : 0;
      acceptedCount.value = totalProcessed;
      currentBatch.value = idx + 1;
      const elapsedMs = Date.now() - runStartedAt;
      const completedBatches = idx + 1;
      const remainingBatches = chunks.length - completedBatches;
      const averageMsPerBatch = elapsedMs / Math.max(1, completedBatches);
      etaSeconds.value = Math.max(0, Math.round((averageMsPerBatch * remainingBatches) / 1000));
      allDuplicateIds.push(...(Array.isArray(data.duplicate_document_ids) ? data.duplicate_document_ids : []));

      // ③ Throttle delay with live countdown
      if (idx < chunks.length - 1 && safeDelaySeconds > 0 && !aborted.value) {
        currentPhase.value = "waiting";
        for (let remaining = safeDelaySeconds; remaining > 0 && !aborted.value; remaining--) {
          if (etaSeconds.value != null) {
            etaSeconds.value = Math.max(0, etaSeconds.value - 1);
          }
          message.value = `次のバッチまで ${remaining}秒 待機中... (${idx + 1}/${chunks.length} 完了)`;
          await sleep(1000);
        }
      }
    }

    if (!aborted.value) {
      currentPhase.value = "done";
      etaSeconds.value = 0;
      if (allDuplicateIds.length > 0) {
        message.value = `アップロード受理。キュー投入: ${totalProcessed}件。既処理ファイル: ${allDuplicateIds.length}件（ID: ${allDuplicateIds.join(", ")}）`;
      } else {
        message.value = `アップロード受理。キュー投入: ${totalProcessed}件。結果は一覧ステータスで確認してください。`;
      }
    }
  } catch (err: any) {
    if ((err as any)?.aborted || err?.message === "upload_aborted") {
      currentPhase.value = "aborted";
      etaSeconds.value = null;
      message.value = "アップロードをキャンセルしました";
      return;
    }
    if (err?.response?.status === 413) {
      currentPhase.value = "error";
      etaSeconds.value = null;
      lastFailureReason.value = "ファイルサイズが上限を超過しました。";
      message.value = "ファイルサイズが大きすぎます。画像を圧縮して再試行してください。";
      return;
    }
    currentPhase.value = "error";
    etaSeconds.value = null;
    const detail = err?.response?.data?.detail;
    if (typeof detail === "string") {
      lastFailureReason.value = detail;
    }
    message.value = typeof detail === "string" ? `アップロード失敗: ${detail}` : "アップロードに失敗しました。再試行してください。";
  } finally {
    uploading.value = false;
    aborted.value = false;
    healthStatusText.value = "";
  }
}
</script>

<template>
  <section class="card">
    <h2>アップロードセンター</h2>
    <p>{{ pageIntro }}</p>

    <input
      id="upload-file-input"
      ref="fileInput"
      class="hidden-file-input"
      type="file"
      multiple
      accept="image/*,application/pdf"
      @change="onFileChange"
    />

    <input
      ref="folderInput"
      class="hidden-file-input"
      type="file"
      multiple
      webkitdirectory
      directory
      accept="image/*,application/pdf"
      @change="onFolderChange"
    />

    <label
      class="dropzone"
      :class="{ active: dragActive, 'mobile-upload-mode': isMobileUploadMode }"
      for="upload-file-input"
      role="button"
      tabindex="0"
      @keydown.enter.prevent="openFileDialog"
      @keydown.space.prevent="openFileDialog"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <p v-if="isMobileUploadMode" class="mobile-mode-badge">スマホ向け: タップで選択</p>
      <p class="dropzone-title">{{ dropzoneTitle }}</p>
      <p class="dropzone-subtitle">{{ dropzoneSubtitle }}</p>
    </label>

    <div v-if="healthStatusText" class="health-warn" role="alert">
      ⚠ {{ healthStatusText }}
      <button class="cancel-btn" @click="aborted = true">中断</button>
    </div>

    <div class="upload-progress" aria-live="polite">
      <div class="upload-progress-header">
        <span class="phase-pill" :class="`phase-pill--${currentPhase}`">{{ phaseLabel }}</span>
        <span class="progress-text">{{ currentBatch }}/{{ totalBatches || 0 }} バッチ</span>
      </div>
      <div class="progress-track" role="progressbar" :aria-valuenow="uploadProgressPercent" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-fill" :style="{ width: `${uploadProgressPercent}%` }"></div>
      </div>
      <p class="progress-summary">受理件数: {{ acceptedCount }} 件</p>
      <p class="progress-summary">推定残り時間 (ETA): {{ etaLabel }}</p>
      <p v-if="lastFailureReason" class="last-failure">直近の失敗理由: {{ lastFailureReason }}</p>
    </div>

    <div class="toolbar">
      <button class="secondary" :disabled="uploading" @click="openFileDialog">ファイルを選択</button>
      <button class="secondary" :disabled="uploading || !supportsFolderSelection" @click="openFolderDialog">
        フォルダを選択して一括取込
      </button>
      <button v-if="!uploading" @click="upload">アップロードして処理</button>
      <button v-else class="danger" @click="aborted = true">中断</button>
    </div>

    <div class="speed-controls" role="group" aria-label="upload-speed-controls">
      <div class="speed-field">
        <label for="batch-size">1バッチあたり件数</label>
        <input id="batch-size" type="number" min="1" max="20" v-model.number="batchSize" :disabled="uploading" />
      </div>
      <div class="speed-field">
        <label for="delay-seconds">バッチ間隔 (秒)</label>
        <input id="delay-seconds" type="number" min="0" max="60" v-model.number="delaySeconds" :disabled="uploading" />
      </div>
      <div class="speed-field">
        <label for="max-retries">失敗時リトライ回数</label>
        <input id="max-retries" type="number" min="0" max="10" v-model.number="maxRetries" :disabled="uploading" />
      </div>
      <div class="speed-field">
        <label for="retry-base">リトライ初回待機 (秒)</label>
        <input id="retry-base" type="number" min="1" max="60" v-model.number="retryBaseSeconds" :disabled="uploading" />
      </div>
    </div>

    <p class="speed-hint">
      推奨: 初回は 1〜2 件/バッチ・3〜5 秒間隔。リトライ 3 回・初回待機 3 秒。処理が安定したら段階的に調整してください。
    </p>
    <p v-if="!supportsFolderSelection" class="speed-hint warn">
      このブラウザはフォルダ選択に未対応です。必要なファイルを複数選択してください。
    </p>

    <p v-if="files.length > 0">選択中: {{ files.map((f) => f.name).join(", ") }}</p>
    <p>{{ message }}</p>
  </section>
</template>

<style scoped>
.hidden-file-input {
  position: fixed;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
  left: -9999px;
  top: -9999px;
}

.dropzone {
  display: block;
  width: 100%;
  position: relative;
  overflow: hidden;
  border: 2px dashed #b7c1cf;
  background: #f7fafc;
  border-radius: 12px;
  padding: 22px 16px;
  margin-bottom: 14px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.dropzone::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(120deg, transparent 20%, rgba(255, 255, 255, 0.55) 40%, transparent 60%);
  transform: translateX(-130%);
  transition: transform 0.45s ease;
  pointer-events: none;
}

.dropzone:focus-visible {
  outline: 2px solid #1a2340;
  outline-offset: 2px;
}

.dropzone:hover {
  border-color: #4f6786;
  background: #edf4fb;
  box-shadow: 0 10px 24px rgba(20, 40, 80, 0.12);
  transform: translateY(-1px);
}

.dropzone:hover::after {
  transform: translateX(130%);
}

.dropzone.active {
  border-color: #bd3f2a;
  background: linear-gradient(180deg, #fff5f2 0%, #ffece6 100%);
  box-shadow: 0 0 0 3px rgba(200, 75, 49, 0.18), 0 12px 26px rgba(184, 67, 43, 0.22), inset 0 0 0 1px #c84b31;
  animation: dropzonePulse 0.95s ease-in-out infinite alternate;
}

.dropzone-title {
  margin: 0;
  font-weight: 700;
}

.dropzone-subtitle {
  margin: 6px 0 0;
  color: #5f6b7a;
  font-size: 13px;
}

.dropzone:hover .dropzone-title,
.dropzone.active .dropzone-title {
  color: #173b67;
}

.dropzone.active .dropzone-subtitle {
  color: #7d2f1f;
}

@keyframes dropzonePulse {
  from {
    box-shadow: 0 0 0 2px rgba(200, 75, 49, 0.16), 0 8px 18px rgba(184, 67, 43, 0.18), inset 0 0 0 1px #c84b31;
  }
  to {
    box-shadow: 0 0 0 4px rgba(200, 75, 49, 0.24), 0 14px 30px rgba(184, 67, 43, 0.26), inset 0 0 0 1px #c84b31;
  }
}

@media (min-width: 901px) {
  .dropzone {
    min-height: 138px;
    display: grid;
    place-content: center;
    gap: 6px;
  }
}

.mobile-mode-badge {
  display: inline-block;
  margin: 0 auto 4px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #dce9f8;
  color: #27476f;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2px;
}

.speed-controls {
  margin-bottom: 10px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.speed-field label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: #4f5f73;
  font-weight: 700;
}

.speed-hint {
  margin: 0 0 8px;
  color: #526177;
  font-size: 12px;
  line-height: 1.5;
}

.speed-hint.warn {
  color: #9a4f12;
}

.health-warn {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fff4e5;
  border: 1px solid #f0a832;
  color: #7a4800;
  font-size: 13px;
  line-height: 1.4;
}

.upload-progress {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #d2dbea;
  background: #f8fbff;
}

.upload-progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.phase-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 700;
}

.phase-pill--idle {
  background: #e5ebf3;
  color: #39516c;
}

.phase-pill--health,
.phase-pill--uploading,
.phase-pill--waiting {
  background: #fff2dc;
  color: #8f5a00;
}

.phase-pill--done {
  background: #e4f5ea;
  color: #1f6d3c;
}

.phase-pill--aborted,
.phase-pill--error {
  background: #fde8e8;
  color: #982a2a;
}

.progress-text {
  font-size: 12px;
  color: #4e6076;
}

.progress-track {
  height: 8px;
  border-radius: 999px;
  background: #e3eaf4;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2f5f9e 0%, #5d87bd 100%);
  transition: width 0.2s ease;
}

.progress-summary {
  margin: 8px 0 0;
  color: #42566f;
  font-size: 12px;
}

.last-failure {
  margin: 8px 0 0;
  padding: 7px 8px;
  border-radius: 7px;
  background: #fff2f2;
  border: 1px solid #efc3c3;
  color: #9a2b2b;
  font-size: 12px;
  line-height: 1.45;
}

.cancel-btn {
  margin-left: auto;
  flex-shrink: 0;
  background: #f0a832;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  width: auto;
}

.cancel-btn:hover {
  background: #d48c1c;
}

button.danger {
  background: #c0392b;
}

@media (max-width: 900px) {
  .dropzone {
    border-radius: 14px;
    padding: 18px 12px;
    min-height: 132px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 6px;
  }

  .dropzone-title {
    font-size: 16px;
    line-height: 1.35;
  }

  .dropzone-subtitle {
    font-size: 12px;
    line-height: 1.5;
  }

  .mobile-upload-mode {
    border-style: solid;
    border-color: #8ea0b7;
    background: linear-gradient(180deg, #f8fbff 0%, #eef4fb 100%);
    box-shadow: inset 0 0 0 1px rgba(142, 160, 183, 0.35);
  }

  .mobile-upload-mode:active {
    transform: scale(0.995);
    background: linear-gradient(180deg, #eef4fb 0%, #e2ecf7 100%);
  }

  .speed-controls {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .health-warn {
    flex-wrap: wrap;
    gap: 8px;
  }

  .upload-progress-header {
    flex-wrap: wrap;
  }

  .cancel-btn {
    margin-left: 0;
    width: 100%;
  }
}
</style>