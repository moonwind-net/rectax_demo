<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api } from "../services/api";

interface Task {
  id: number;
  document_id: number;
  task_status: string;
  reason_codes: string[];
  original_filename?: string | null;
  merchant_name?: string | null;
  total_amount?: number | null;
  has_critical?: boolean;
}

interface Subject {
  id: number;
  subject_code: string;
  subject_name: string;
}

interface CorrectionTemplate {
  id: number;
  template_key: string;
  label: string;
  merchant_pattern?: string | null;
  patch_fields: Record<string, string>;
  note_prefix?: string | null;
  priority: number;
  matched: boolean;
}

interface TaxLineForm {
  id?: number;
  tax_rate: string;
  taxable_amount: string;
  tax_amount: string;
  is_reduced_tax: boolean;
}

interface PreviewCacheEntry {
  url: string;
  contentType: string;
  loading: boolean;
  error: boolean;
}

interface TaxConfig {
  jpy_rounding_mode?: "floor" | "round" | "ceil";
  tax_rounding_level?: "document" | "tax_rate" | "line";
}

interface AmountRoleCandidate {
  tendered?: number | null;
  change?: number | null;
  total?: number | null;
  tax?: number | null;
  subtotal?: number | null;
  tax_rate?: number | null;
  tax_rate_label?: string | null;
  score?: number | null;
  score_breakdown?: Record<string, number> | null;
  line_refs?: {
    tendered_line?: number | null;
    change_line?: number | null;
  } | null;
}

interface ReviewContext {
  task: Task & { resolution_note?: string | null; resolved_subject_id?: number | null };
  document: {
    id: number;
    original_filename: string;
    document_status: string;
    updated_at?: string | null;
  };
  extraction: {
    merchant_name?: string | null;
    transaction_date?: string | null;
    registration_number?: string | null;
    tax_amount?: number | null;
    total_amount?: number | null;
    tax_rate_label?: string | null;
    confidence?: number | null;
    amount_role_selected?: AmountRoleCandidate | null;
    amount_role_candidates?: AmountRoleCandidate[];
  };
  tax_lines: Array<{
    id?: number;
    tax_rate?: number | null;
    taxable_amount?: number | null;
    tax_amount?: number | null;
    is_reduced_tax?: boolean;
  }>;
  classification: {
    subject_id?: number | null;
    confidence?: number | null;
    decision_source?: string | null;
  };
  tax_config?: TaxConfig;
  subjects: Subject[];
  audit_logs: Array<{
    id: number;
    action_type: string;
    changed_by?: number | null;
    reason_note?: string | null;
    before_json?: Record<string, any> | null;
    after_json?: Record<string, any> | null;
    created_at?: string | null;
  }>;
  correction_templates: CorrectionTemplate[];
}

const tasks = ref<Task[]>([]);
const selectedTaskId = ref<number | null>(null);
const context = ref<ReviewContext | null>(null);
const loadingContext = ref(false);
const actionMessage = ref("");
const previewUrl = ref("");
const previewType = ref("");
const imageZoom = ref(1);
const imageOffset = ref({ x: 0, y: 0 });
const isPanning = ref(false);
const panStart = ref({ x: 0, y: 0, originX: 0, originY: 0 });
const queueSearch = ref("");
const queueFilterCode = ref<string | null>(null);
const formTaxLines = ref<TaxLineForm[]>([]);
const previewCache = ref<Record<number, PreviewCacheEntry>>({});
const successToast = ref("");
let successToastTimer: ReturnType<typeof setTimeout> | null = null;

function showSuccessToast(message: string) {
  successToast.value = message;
  if (successToastTimer) {
    clearTimeout(successToastTimer);
  }
  successToastTimer = setTimeout(() => {
    successToast.value = "";
    successToastTimer = null;
  }, 2600);
}

const correctionTemplates = computed<CorrectionTemplate[]>(
  () => context.value?.correction_templates ?? []
);

const currentTaxConfig = computed<TaxConfig>(() => context.value?.tax_config ?? { jpy_rounding_mode: "round", tax_rounding_level: "tax_rate" });

// All distinct reason codes in the current task list
const availableReasonCodes = computed<string[]>(() => {
  const set = new Set<string>();
  for (const t of tasks.value) for (const c of t.reason_codes) set.add(c);
  return Array.from(set);
});

const filteredTasks = computed<Task[]>(() => {
  const q = queueSearch.value.trim().toLowerCase();
  return tasks.value.filter((t) => {
    if (queueFilterCode.value && !t.reason_codes.includes(queueFilterCode.value)) return false;
    if (q) {
      const haystack = [
        t.original_filename ?? "",
        t.merchant_name ?? "",
        String(t.id),
        String(t.document_id),
      ].join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
});

const currentTaskIndex = computed(() => {
  if (selectedTaskId.value === null) return -1;
  return filteredTasks.value.findIndex((t) => t.id === selectedTaskId.value);
});

const thumbnailTasks = computed<Task[]>(() => {
  const all = filteredTasks.value;
  if (all.length <= 8) return all;
  const currentIndex = currentTaskIndex.value >= 0 ? currentTaskIndex.value : 0;
  const start = Math.max(0, currentIndex - 3);
  const end = Math.min(all.length, start + 8);
  return all.slice(Math.max(0, end - 8), end);
});

function goNext() {
  const arr = filteredTasks.value;
  if (arr.length === 0) return;
  const nextIdx = currentTaskIndex.value < arr.length - 1 ? currentTaskIndex.value + 1 : 0;
  selectedTaskId.value = arr[nextIdx].id;
  loadContext();
}

function goPrev() {
  const arr = filteredTasks.value;
  if (arr.length === 0) return;
  const prevIdx = currentTaskIndex.value > 0 ? currentTaskIndex.value - 1 : arr.length - 1;
  selectedTaskId.value = arr[prevIdx].id;
  loadContext();
}

function onKeyDown(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement).tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); goNext(); }
  if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); goPrev(); }
}

const form = ref({
  merchant_name: "",
  transaction_date: "",
  registration_number: "",
  tax_amount: "",
  total_amount: "",
  tax_rate_label: "unknown",
  subject_id: "",
  note: "",
});

const taxAmountManuallyEdited = ref(false);
const lastAutoTaxAmount = ref("");

function parseTaxRatePercent(label: string): number | null {
  if (label === "8%" || label === "8") return 8;
  if (label === "10%" || label === "10") return 10;
  return null;
}

function normalizeTaxRateLabel(value: string | null | undefined): string {
  const text = String(value ?? "").trim().toLowerCase().replace("％", "%");
  if (text === "8" || text === "8%") return "8";
  if (text === "10" || text === "10%") return "10";
  if (text === "mixed") return "mixed";
  return "unknown";
}

function parseFlexibleRatePercent(label: string): number | null {
  const normalized = String(label).replace("%", "").trim();
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function roundYen(value: number): number {
  const mode = currentTaxConfig.value.jpy_rounding_mode ?? "round";
  if (mode === "floor") return Math.floor(value);
  if (mode === "ceil") return Math.ceil(value);
  return Math.round(value);
}

function roundCurrency(value: number): string {
  return String(roundYen(value));
}

function normalizeMoneyInput(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "";
  return String(roundYen(numberValue));
}

function formatYenLabel(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "（金額未入力）";
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "（金額未入力）";
  return `${roundYen(numberValue).toLocaleString()}円`;
}

function taxLevelLabel(level?: string): string {
  if (level === "document") return "単票全体で取整";
  if (level === "line") return "明細行ごとに取整";
  return "税率分组ごとに取整";
}

function roundingModeLabel(mode?: string): string {
  if (mode === "floor") return "切り捨て";
  if (mode === "ceil") return "切り上げ";
  return "四捨五入";
}

function calculateTaxAmount(totalAmount: string, taxRateLabel: string): string {
  const total = Number(totalAmount);
  const rate = parseTaxRatePercent(taxRateLabel);
  if (!Number.isFinite(total) || total <= 0 || rate === null) return "";
  return roundCurrency((total * rate) / (100 + rate));
}

function recalculateTaxAmount(force = false) {
  const nextTaxAmount = calculateTaxAmount(form.value.total_amount, form.value.tax_rate_label);
  if (!nextTaxAmount) {
    if (force || form.value.tax_amount === lastAutoTaxAmount.value) {
      form.value.tax_amount = "";
      lastAutoTaxAmount.value = "";
      taxAmountManuallyEdited.value = false;
    }
    return;
  }

  if (force || !taxAmountManuallyEdited.value || form.value.tax_amount === lastAutoTaxAmount.value || !form.value.tax_amount) {
    form.value.tax_amount = nextTaxAmount;
    lastAutoTaxAmount.value = nextTaxAmount;
    taxAmountManuallyEdited.value = false;
  }
}

function onTaxAmountInput() {
  taxAmountManuallyEdited.value = form.value.tax_amount !== lastAutoTaxAmount.value;
}

function createDefaultMixedTaxLines(): TaxLineForm[] {
  return [
    { tax_rate: "8", taxable_amount: "", tax_amount: "", is_reduced_tax: true },
    { tax_rate: "10", taxable_amount: "", tax_amount: "", is_reduced_tax: false },
  ];
}

function normalizeTaxLines(lines: ReviewContext["tax_lines"] | undefined): TaxLineForm[] {
  if (!lines || lines.length === 0) return createDefaultMixedTaxLines();
  return lines.map((line) => ({
    id: line.id,
    tax_rate: line.tax_rate == null ? "" : String(line.tax_rate).replace(/\.0+$/, ""),
    taxable_amount: normalizeMoneyInput(line.taxable_amount),
    tax_amount: normalizeMoneyInput(line.tax_amount),
    is_reduced_tax: Boolean(line.is_reduced_tax),
  }));
}

function calculateLineTaxAmount(taxableAmount: string, taxRate: string): string {
  const taxable = Number(taxableAmount);
  const rate = parseFlexibleRatePercent(taxRate);
  if (!Number.isFinite(taxable) || taxable < 0 || rate === null) return "";
  return roundCurrency((taxable * rate) / 100);
}

function allocateByCumulative(rawValues: number[]): number[] {
  const assigned: number[] = [];
  let cumulativeRaw = 0;
  let cumulativeAssigned = 0;
  for (const rawValue of rawValues) {
    cumulativeRaw += rawValue;
    const nextCumulativeTarget = roundYen(cumulativeRaw);
    const nextValue = nextCumulativeTarget - cumulativeAssigned;
    assigned.push(nextValue);
    cumulativeAssigned += nextValue;
  }
  return assigned;
}

function recalculateTaxLinesByConfig(lines: TaxLineForm[]): TaxLineForm[] {
  const prepared = lines.map((line) => ({
    ...line,
    taxable_amount: normalizeMoneyInput(line.taxable_amount),
    rawTax: line.taxable_amount && line.tax_rate
      ? Number(normalizeMoneyInput(line.taxable_amount)) * (Number(line.tax_rate) / 100)
      : 0,
  }));

  const level = currentTaxConfig.value.tax_rounding_level ?? "tax_rate";
  let allocated: number[] = [];

  if (level === "line") {
    allocated = prepared.map((line) => roundYen(line.rawTax));
  } else if (level === "document") {
    allocated = allocateByCumulative(prepared.map((line) => line.rawTax));
  } else {
    allocated = new Array(prepared.length).fill(0);
    const groupedIndexes = new Map<string, number[]>();
    prepared.forEach((line, index) => {
      const key = line.tax_rate || "";
      const indexes = groupedIndexes.get(key) ?? [];
      indexes.push(index);
      groupedIndexes.set(key, indexes);
    });
    groupedIndexes.forEach((indexes) => {
      const groupAllocated = allocateByCumulative(indexes.map((index) => prepared[index].rawTax));
      indexes.forEach((index, position) => {
        allocated[index] = groupAllocated[position];
      });
    });
  }

  return prepared.map((line, index) => ({
    id: line.id,
    tax_rate: line.tax_rate,
    taxable_amount: line.taxable_amount,
    tax_amount: line.taxable_amount && line.tax_rate ? String(allocated[index]) : "",
    is_reduced_tax: parseFlexibleRatePercent(line.tax_rate) === 8,
  }));
}

function syncMixedTaxLines() {
  formTaxLines.value = recalculateTaxLinesByConfig(formTaxLines.value);

  if (form.value.tax_rate_label !== "mixed") {
    return;
  }

  const totalTax = formTaxLines.value.reduce((sum, line) => sum + (Number(line.tax_amount) || 0), 0);
  const normalizedTax = totalTax > 0 ? String(totalTax) : "";
  form.value.tax_amount = normalizedTax;
  lastAutoTaxAmount.value = normalizedTax;
  taxAmountManuallyEdited.value = false;
}

function addTaxLine() {
  formTaxLines.value = [...formTaxLines.value, { tax_rate: "10", taxable_amount: "", tax_amount: "", is_reduced_tax: false }];
  syncMixedTaxLines();
}

function removeTaxLine(index: number) {
  formTaxLines.value = formTaxLines.value.filter((_, currentIndex) => currentIndex !== index);
  if (formTaxLines.value.length === 0) {
    formTaxLines.value = createDefaultMixedTaxLines();
  }
  syncMixedTaxLines();
}

function serializeTaxLines() {
  return formTaxLines.value
    .filter((line) => line.tax_rate && line.taxable_amount)
    .map((line) => ({
      tax_rate: Number(line.tax_rate),
      taxable_amount: Number(line.taxable_amount),
      tax_amount: line.tax_amount === "" ? null : Number(line.tax_amount),
      is_reduced_tax: line.is_reduced_tax,
    }));
}

function guessPreviewKind(task: Task): "image" | "pdf" | "unknown" {
  const cached = previewCache.value[task.document_id];
  if (cached?.contentType.startsWith("image/")) return "image";
  if (cached?.contentType.includes("pdf")) return "pdf";
  const name = (task.original_filename ?? "").toLowerCase();
  if (/\.(png|jpg|jpeg|webp|gif|bmp)$/.test(name)) return "image";
  if (name.endsWith(".pdf")) return "pdf";
  return "unknown";
}

async function ensurePreviewAsset(documentId: number) {
  const existing = previewCache.value[documentId];
  if (existing && (existing.loading || existing.url || existing.error)) {
    return existing;
  }

  previewCache.value = {
    ...previewCache.value,
    [documentId]: { url: "", contentType: "", loading: true, error: false },
  };

  try {
    const res = await api.get(`/documents/${documentId}/preview`, { responseType: "blob" });
    const nextEntry = {
      url: URL.createObjectURL(res.data),
      contentType: res.headers["content-type"] ?? "",
      loading: false,
      error: false,
    };
    previewCache.value = {
      ...previewCache.value,
      [documentId]: nextEntry,
    };
    return nextEntry;
  } catch {
    const failedEntry = { url: "", contentType: "", loading: false, error: true };
    previewCache.value = {
      ...previewCache.value,
      [documentId]: failedEntry,
    };
    return failedEntry;
  }
}

watch(
  [() => form.value.total_amount, () => form.value.tax_rate_label],
  () => {
    recalculateTaxAmount(false);
  }
);

watch(
  () => form.value.tax_rate_label,
  (taxRateLabel) => {
    if (taxRateLabel === "mixed") {
      if (formTaxLines.value.length === 0) {
        formTaxLines.value = createDefaultMixedTaxLines();
      }
      syncMixedTaxLines();
    }
  }
);

watch(
  thumbnailTasks,
  (tasksForStrip) => {
    tasksForStrip.forEach((task) => {
      void ensurePreviewAsset(task.document_id);
    });
  },
  { immediate: true }
);

const reasonLabelMap: Record<string, { label: string; level: "critical" | "warning" }> = {
  low_ocr_confidence: { label: "OCR信頼度が低い", level: "critical" },
  low_classification_confidence: { label: "分類信頼度が低い", level: "warning" },
  unknown_tax_rate: { label: "税率が未確定", level: "critical" },
  missing_merchant: { label: "店舗名が未抽出", level: "critical" },
  missing_total_amount: { label: "合計金額が未抽出", level: "critical" },
};

const requiredMissing = computed(() => {
  const missing: string[] = [];
  if (!form.value.merchant_name.trim()) missing.push("店舗名");
  if (!form.value.total_amount.toString().trim()) missing.push("合計金額");
  if (!form.value.tax_rate_label || form.value.tax_rate_label === "unknown") missing.push("税率");
  if (!form.value.subject_id) missing.push("勘定科目");
  return missing;
});

const canApprove = computed(() => requiredMissing.value.length === 0);

const selectedSubject = computed(() => {
  if (!context.value || !form.value.subject_id) return null;
  return context.value.subjects.find((s) => s.id === Number(form.value.subject_id)) ?? null;
});

const postingPreview = computed(() => {
  return {
    entryDate: form.value.transaction_date || "（取引日未入力）",
    debitSubject: selectedSubject.value
      ? `${selectedSubject.value.subject_code} ${selectedSubject.value.subject_name}`
      : "（科目未選択）",
    amount: formatYenLabel(form.value.total_amount),
    taxRate: form.value.tax_rate_label || "unknown",
    merchant: form.value.merchant_name || "（店舗名未入力）",
    registration: form.value.registration_number || "-",
    note: form.value.note || "-",
  };
});

const previewImageStyle = computed(() => ({
  transform: `translate(${imageOffset.value.x}px, ${imageOffset.value.y}px) scale(${imageZoom.value})`,
  transition: isPanning.value ? "none" : "transform 120ms ease-out",
}));

function resetPreviewImageViewport() {
  imageZoom.value = 1;
  imageOffset.value = { x: 0, y: 0 };
  isPanning.value = false;
}

function setPreviewImageZoom(nextZoom: number) {
  const clamped = Math.max(1, Math.min(6, nextZoom));
  imageZoom.value = clamped;
  if (clamped === 1) {
    imageOffset.value = { x: 0, y: 0 };
  }
}

function zoomInPreviewImage() {
  setPreviewImageZoom(imageZoom.value * 1.2);
}

function zoomOutPreviewImage() {
  setPreviewImageZoom(imageZoom.value / 1.2);
}

function onPreviewImageWheel(event: WheelEvent) {
  if (!previewType.value.startsWith("image/")) return;
  event.preventDefault();
  if (event.deltaY < 0) {
    zoomInPreviewImage();
  } else {
    zoomOutPreviewImage();
  }
}

function onPreviewImageMouseDown(event: MouseEvent) {
  if (imageZoom.value <= 1) return;
  isPanning.value = true;
  panStart.value = {
    x: event.clientX,
    y: event.clientY,
    originX: imageOffset.value.x,
    originY: imageOffset.value.y,
  };
}

function onPreviewImageMouseMove(event: MouseEvent) {
  if (!isPanning.value) return;
  const dx = event.clientX - panStart.value.x;
  const dy = event.clientY - panStart.value.y;
  imageOffset.value = {
    x: panStart.value.originX + dx,
    y: panStart.value.originY + dy,
  };
}

function onPreviewImageMouseUp() {
  isPanning.value = false;
}

const amountRoleCandidates = computed<AmountRoleCandidate[]>(() => {
  return context.value?.extraction?.amount_role_candidates ?? [];
});

const amountRoleSelected = computed<AmountRoleCandidate | null>(() => {
  return context.value?.extraction?.amount_role_selected ?? null;
});

function formatMoney(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `¥${Math.round(Number(value)).toLocaleString()}`;
}

function scoreBreakdownEntries(candidate: AmountRoleCandidate): Array<{ key: string; value: number }> {
  const breakdown = candidate.score_breakdown ?? {};
  return Object.entries(breakdown)
    .map(([key, value]) => ({ key, value: Number(value) }))
    .sort((a, b) => b.value - a.value);
}

function scoreKeyLabel(key: string): string {
  const map: Record<string, string> = {
    tendered_keyword: "現金キーワード一致",
    change_keyword: "お釣りキーワード一致",
    tendered_near_payment_line: "現金行に近い",
    change_near_change_line: "お釣り行に近い",
    subtotal_observed: "未税額が票面に存在",
    tax_observed: "税額が票面に存在",
    total_observed: "合計が票面に存在",
    tax_formula_consistency: "税計算整合",
  };
  return map[key] ?? key;
}

const highlightedFields = computed(() => {
  const codes = context.value?.task.reason_codes ?? [];
  return {
    merchant: codes.includes("low_ocr_confidence") || codes.includes("missing_merchant"),
    amount: codes.includes("low_ocr_confidence") || codes.includes("missing_total_amount"),
    taxRate: codes.includes("unknown_tax_rate") || codes.includes("low_ocr_confidence"),
    subject: codes.includes("low_classification_confidence"),
  };
});

function reasonClass(code: string) {
  return reasonLabelMap[code]?.level === "critical" ? "reason-pill reason-pill--critical" : "reason-pill reason-pill--warning";
}

function reasonLabel(code: string) {
  return reasonLabelMap[code]?.label ?? code;
}

function applyCorrectionTemplate(tpl: CorrectionTemplate) {
  const pf = tpl.patch_fields;
  if (pf.merchant_name) form.value.merchant_name = pf.merchant_name;
  if (pf.tax_rate_label) form.value.tax_rate_label = normalizeTaxRateLabel(pf.tax_rate_label);
  const prefix = tpl.note_prefix ?? pf.notePrefix;
  if (prefix) {
    form.value.note = form.value.note
      ? `${prefix}\n${form.value.note}`
      : prefix;
  }
  actionMessage.value = `テンプレ「${tpl.label}」を適用しました。`;
}

function jsonValueToText(value: any): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const diffFieldLabels: Record<string, string> = {
  document_status: "書類ステータス",
  task_status: "レビュータスク状態",
  resolved_subject_id: "確定科目ID",
  tax_lines: "税率別明細",
  "extraction.merchant_name": "店舗名",
  "extraction.transaction_date": "取引日",
  "extraction.registration_number": "登録番号",
  "extraction.tax_amount": "税額",
  "extraction.total_amount": "合計金額",
  "extraction.tax_rate_label": "税率",
  "extraction.confidence": "OCR信頼度",
};

function getAuditDiffRows(log: ReviewContext["audit_logs"][number]) {
  const before = log.before_json ?? {};
  const after = log.after_json ?? {};
  const rows: Array<{ key: string; label: string; before: string; after: string }> = [];

  const topKeys = ["document_status", "task_status", "resolved_subject_id"];
  for (const key of topKeys) {
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) {
      rows.push({
        key,
        label: diffFieldLabels[key] ?? key,
        before: jsonValueToText(before[key]),
        after: jsonValueToText(after[key]),
      });
    }
  }

  const beforeExtraction = before.extraction ?? {};
  const afterExtraction = after.extraction ?? {};
  const extractionKeys = [
    "merchant_name",
    "transaction_date",
    "registration_number",
    "tax_amount",
    "total_amount",
    "tax_rate_label",
    "confidence",
  ];
  for (const key of extractionKeys) {
    if (JSON.stringify(beforeExtraction[key]) !== JSON.stringify(afterExtraction[key])) {
      const fullKey = `extraction.${key}`;
      rows.push({
        key: fullKey,
        label: diffFieldLabels[fullKey] ?? fullKey,
        before: jsonValueToText(beforeExtraction[key]),
        after: jsonValueToText(afterExtraction[key]),
      });
    }
  }

  if (JSON.stringify(before.tax_lines ?? []) !== JSON.stringify(after.tax_lines ?? [])) {
    rows.push({
      key: "tax_lines",
      label: diffFieldLabels.tax_lines,
      before: jsonValueToText(before.tax_lines ?? []),
      after: jsonValueToText(after.tax_lines ?? []),
    });
  }
  return rows;
}

async function load() {
  const { data } = await api.get("/review/tasks");
  tasks.value = data;

  if (tasks.value.length === 0) {
    selectedTaskId.value = null;
    context.value = null;
    return;
  }

  const visible = filteredTasks.value;
  if (!selectedTaskId.value || !visible.some((t) => t.id === selectedTaskId.value)) {
    selectedTaskId.value = visible.length > 0 ? visible[0].id : tasks.value[0].id;
  }

  await loadContext();
}

onMounted(() => {
  load();
  window.addEventListener("keydown", onKeyDown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown);
  if (successToastTimer) {
    clearTimeout(successToastTimer);
    successToastTimer = null;
  }
  Object.values(previewCache.value).forEach((entry) => {
    if (entry.url) URL.revokeObjectURL(entry.url);
  });
});

async function loadPreview(documentId: number) {
  const cached = await ensurePreviewAsset(documentId);
  if (cached?.url) {
    previewType.value = cached.contentType;
    previewUrl.value = cached.url;
    resetPreviewImageViewport();
  } else {
    previewUrl.value = "";
    previewType.value = "";
    resetPreviewImageViewport();
  }
}

async function loadContext() {
  if (!selectedTaskId.value) return;
  loadingContext.value = true;
  actionMessage.value = "";
  try {
    const { data } = await api.get(`/review/tasks/${selectedTaskId.value}/context`);
    context.value = data;
    form.value = {
      merchant_name: data.extraction?.merchant_name ?? "",
      transaction_date: data.extraction?.transaction_date ?? "",
      registration_number: data.extraction?.registration_number ?? "",
      tax_amount: normalizeMoneyInput(data.extraction?.tax_amount),
      total_amount: normalizeMoneyInput(data.extraction?.total_amount),
      tax_rate_label: normalizeTaxRateLabel(data.extraction?.tax_rate_label ?? "unknown"),
      subject_id: data.task?.resolved_subject_id != null
        ? String(data.task.resolved_subject_id)
        : data.classification?.subject_id != null
          ? String(data.classification.subject_id)
          : "",
      note: data.task?.resolution_note ?? "",
    };
    formTaxLines.value = data.extraction?.tax_rate_label === "mixed"
      ? normalizeTaxLines(data.tax_lines)
      : normalizeTaxLines(data.tax_lines ?? []).filter((line) => line.taxable_amount || line.tax_amount);
    lastAutoTaxAmount.value = form.value.tax_amount;
    taxAmountManuallyEdited.value = false;
    if (form.value.tax_rate_label === "mixed") {
      syncMixedTaxLines();
    }
    await loadPreview(data.document.id);
  } finally {
    loadingContext.value = false;
  }
}

function applyRecommendedSubject() {
  const recommendedId = context.value?.classification?.subject_id;
  if (!recommendedId) {
    actionMessage.value = "推奨科目がありません。手動で選択してください。";
    return;
  }
  form.value.subject_id = String(recommendedId);
  actionMessage.value = "推奨科目を適用しました。";
}

async function rerunCurrentOcr() {
  if (!context.value) return;
  const documentId = context.value.document.id;
  await api.post(`/documents/${documentId}/re-ocr`);
  actionMessage.value = `書類ID ${documentId} を OCR 再解析キューに追加しました。`;
  await load();
}

async function approveCurrent() {
  if (!context.value) return;
  if (!canApprove.value) {
    actionMessage.value = `承認できません。必須項目: ${requiredMissing.value.join(" / ")}`;
    return;
  }
  const currentTask = context.value.task;
  const orderedIds = filteredTasks.value.map((t) => t.id);
  const currentIndex = orderedIds.indexOf(currentTask.id);
  const hasMoreThanOne = orderedIds.length > 1;
  const preferredNextId =
    hasMoreThanOne && currentIndex >= 0
      ? orderedIds[(currentIndex + 1) % orderedIds.length]
      : null;

  try {
    await api.post(`/review/tasks/${currentTask.id}/resolve`, {
      action: "approve",
      subject_id: Number(form.value.subject_id),
      note: form.value.note,
      corrected_extraction: {
        merchant_name: form.value.merchant_name,
        transaction_date: form.value.transaction_date || null,
        registration_number: form.value.registration_number || null,
        tax_amount: form.value.tax_amount === "" ? null : Number(form.value.tax_amount),
        total_amount: form.value.total_amount === "" ? null : Number(form.value.total_amount),
        tax_rate_label: normalizeTaxRateLabel(form.value.tax_rate_label),
      },
      corrected_tax_lines: form.value.tax_rate_label === "mixed" ? serializeTaxLines() : [],
    });
  } catch (err: any) {
    actionMessage.value = err?.response?.data?.detail ?? "承認に失敗しました。入力値を確認して再試行してください。";
    return;
  }
  actionMessage.value = "修正内容を保存し、承認しました。";
  await load();

  // Auto-advance to the next pending task and show explicit success feedback.
  if (tasks.value.length > 0) {
    const nextTaskId =
      preferredNextId && tasks.value.some((t) => t.id === preferredNextId)
        ? preferredNextId
        : tasks.value[0].id;
    selectedTaskId.value = nextTaskId;
    await loadContext();
    const nextTask = tasks.value.find((t) => t.id === nextTaskId);
    const nextLabel = nextTask?.merchant_name || nextTask?.original_filename || `書類 #${nextTask?.document_id ?? "-"}`;
    showSuccessToast(`承認成功：タスク #${currentTask.id}。次のレビューへ移動しました（${nextLabel}）。`);
  } else {
    showSuccessToast(`承認成功：タスク #${currentTask.id}。レビュー待ちは残っていません。`);
  }
}

async function requestCorrection() {
  if (!context.value) return;
  if (!form.value.note.trim()) {
    actionMessage.value = "差戻し理由を入力してください。";
    return;
  }
  try {
    await api.post(`/review/tasks/${context.value.task.id}/resolve`, {
      action: "reject",
      note: form.value.note,
      corrected_extraction: {
        merchant_name: form.value.merchant_name,
        transaction_date: form.value.transaction_date || null,
        registration_number: form.value.registration_number || null,
        tax_amount: form.value.tax_amount === "" ? null : Number(form.value.tax_amount),
        total_amount: form.value.total_amount === "" ? null : Number(form.value.total_amount),
        tax_rate_label: normalizeTaxRateLabel(form.value.tax_rate_label),
      },
      corrected_tax_lines: form.value.tax_rate_label === "mixed" ? serializeTaxLines() : [],
    });
  } catch (err: any) {
    actionMessage.value = err?.response?.data?.detail ?? "差戻しに失敗しました。しばらくして再試行してください。";
    return;
  }
  actionMessage.value = "差戻ししました。修正後に再承認できます。";
  await load();
}
</script>

<template>
  <section class="card">
    <div class="toolbar">
      <h2>レビューキュー V1</h2>
      <button class="secondary" @click="load">再読み込み</button>
      <button class="secondary" :disabled="!context || loadingContext" @click="rerunCurrentOcr">この票をOCR再解析</button>
    </div>

    <div v-if="actionMessage" class="action-message">{{ actionMessage }}</div>
    <div v-if="successToast" class="success-toast" role="status" aria-live="polite">{{ successToast }}</div>

    <div class="review-layout">
      <aside class="task-list-panel">
        <!-- Header: title + counter + nav -->
        <div class="queue-header">
          <span class="panel-title" style="margin:0">
            要確認タスク
            <span class="queue-count">
              {{ filteredTasks.length > 0 ? `${currentTaskIndex + 1} / ${filteredTasks.length}` : `0 / ${tasks.length}` }}
            </span>
          </span>
          <div class="queue-nav">
            <button class="nav-btn" :disabled="filteredTasks.length === 0" title="前の件 (↑ / K)" @click="goPrev">▲</button>
            <button class="nav-btn" :disabled="filteredTasks.length === 0" title="次の件 (↓ / J)" @click="goNext">▼</button>
          </div>
        </div>

        <!-- Search -->
        <input
          v-model="queueSearch"
          class="queue-search"
          type="search"
          placeholder="ファイル名・店舗名で絞込..."
        />

        <!-- Reason code filter chips -->
        <div class="queue-filter-chips" v-if="availableReasonCodes.length > 0">
          <button
            class="filter-chip"
            :class="{ 'filter-chip--active': queueFilterCode === null }"
            @click="queueFilterCode = null"
          >全て</button>
          <button
            v-for="code in availableReasonCodes"
            :key="code"
            class="filter-chip"
            :class="[{ 'filter-chip--active': queueFilterCode === code }, reasonLabelMap[code]?.level === 'critical' ? 'filter-chip--critical' : 'filter-chip--warning']"
            @click="queueFilterCode = queueFilterCode === code ? null : code"
          >{{ reasonLabel(code) }}</button>
        </div>

        <!-- Task cards -->
        <div v-if="filteredTasks.length === 0" class="queue-empty">
          該当するタスクがありません
        </div>
        <button
          v-for="task in filteredTasks"
          :key="task.id"
          class="task-card"
          :class="{ 'task-card--active': task.id === selectedTaskId, 'task-card--critical': task.has_critical }"
          @click="selectedTaskId = task.id; loadContext()"
        >
          <div class="task-card-top">
            <span class="task-merchant">{{ task.merchant_name || task.original_filename || `書類 #${task.document_id}` }}</span>
            <span v-if="task.total_amount != null" class="task-amount">¥{{ task.total_amount.toLocaleString() }}</span>
          </div>
          <p class="task-id">#{{ task.id }}</p>
          <div class="reason-list">
            <span v-for="code in task.reason_codes" :key="`${task.id}-${code}`" :class="reasonClass(code)">
              {{ reasonLabel(code) }}
            </span>
          </div>
        </button>
      </aside>

      <div class="work-panel" v-if="context">
        <section class="pane preview-pane">
          <p class="panel-title">票据プレビュー</p>
          <p class="doc-meta">{{ context.document.original_filename }}</p>
          <template v-if="previewUrl && previewType.startsWith('image/')">
            <div
              class="preview-image-stage"
              @wheel.prevent="onPreviewImageWheel"
              @mousedown="onPreviewImageMouseDown"
              @mousemove="onPreviewImageMouseMove"
              @mouseup="onPreviewImageMouseUp"
              @mouseleave="onPreviewImageMouseUp"
              @dblclick="resetPreviewImageViewport"
            >
              <img
                :src="previewUrl"
                alt="receipt preview"
                class="preview-image"
                :class="{ 'preview-image--grabbable': imageZoom > 1, 'preview-image--grabbing': isPanning }"
                :style="previewImageStyle"
                draggable="false"
              />
            </div>
            <div class="preview-zoom-toolbar">
              <button class="secondary" type="button" @click="zoomOutPreviewImage" :disabled="imageZoom <= 1">−</button>
              <span>{{ Math.round(imageZoom * 100) }}%</span>
              <button class="secondary" type="button" @click="zoomInPreviewImage" :disabled="imageZoom >= 6">＋</button>
              <button class="secondary" type="button" @click="resetPreviewImageViewport" :disabled="imageZoom === 1 && imageOffset.x === 0 && imageOffset.y === 0">重置</button>
              <span class="zoom-hint">滚轮缩放 / 按住拖动 / 双击重置</span>
            </div>
          </template>
          <template v-else-if="previewUrl && previewType.includes('pdf')">
            <iframe :src="previewUrl" class="preview-pdf" title="receipt pdf preview"></iframe>
          </template>
          <p v-else class="preview-empty">プレビューを表示できません。OCR結果を参照して確認してください。</p>

          <div class="thumbnail-strip">
            <button
              v-for="task in thumbnailTasks"
              :key="`thumb-${task.id}`"
              class="thumbnail-card"
              :class="{ 'thumbnail-card--active': task.id === selectedTaskId }"
              @click="selectedTaskId = task.id; loadContext()"
            >
              <div class="thumbnail-media">
                <img
                  v-if="guessPreviewKind(task) === 'image' && previewCache[task.document_id]?.url"
                  :src="previewCache[task.document_id].url"
                  :alt="task.original_filename || `document-${task.document_id}`"
                  class="thumbnail-image"
                />
                <div v-else-if="guessPreviewKind(task) === 'pdf'" class="thumbnail-placeholder">PDF</div>
                <div v-else-if="previewCache[task.document_id]?.loading" class="thumbnail-placeholder">...</div>
                <div v-else class="thumbnail-placeholder">FILE</div>
              </div>
              <span class="thumbnail-label">{{ task.merchant_name || task.original_filename || `#${task.document_id}` }}</span>
            </button>
          </div>
        </section>

        <section class="pane edit-pane">
          <p class="panel-title">修正・承認</p>
          <div class="reason-list reason-list--inline">
            <span v-for="code in context.task.reason_codes" :key="`selected-${code}`" :class="reasonClass(code)">
              {{ reasonLabel(code) }}
            </span>
          </div>

          <section class="amount-role-panel">
            <div class="amount-role-head">
              <p class="panel-title">金额角色候選 Top3</p>
              <p class="amount-role-hint">系统按候选分数排序，便于快速判断为何选中该金额组合。</p>
            </div>

            <div v-if="amountRoleSelected" class="amount-role-selected">
              <strong>採用候補:</strong>
              <span>
                現金 {{ formatMoney(amountRoleSelected.tendered) }}
                / お釣り {{ formatMoney(amountRoleSelected.change) }}
                / 合計 {{ formatMoney(amountRoleSelected.total) }}
                / 税 {{ formatMoney(amountRoleSelected.tax) }}
                / 小計 {{ formatMoney(amountRoleSelected.subtotal) }}
                / score {{ amountRoleSelected.score ?? '-' }}
              </span>
            </div>

            <p v-if="amountRoleCandidates.length === 0" class="preview-empty">候補データはありません（旧OCR結果または低信頼票据）。</p>

            <div v-for="(candidate, idx) in amountRoleCandidates" :key="`amount-role-${idx}`" class="amount-role-card">
              <div class="amount-role-summary">
                <p>#{{ idx + 1 }} score {{ candidate.score ?? '-' }}</p>
                <p>
                  現金 {{ formatMoney(candidate.tendered) }} /
                  お釣り {{ formatMoney(candidate.change) }} /
                  合計 {{ formatMoney(candidate.total) }} /
                  税 {{ formatMoney(candidate.tax) }} /
                  小計 {{ formatMoney(candidate.subtotal) }} /
                  税率 {{ candidate.tax_rate_label ?? '-' }}
                </p>
                <p class="amount-role-lines">
                  line: tendered {{ candidate.line_refs?.tendered_line ?? '-' }},
                  change {{ candidate.line_refs?.change_line ?? '-' }}
                </p>
              </div>

              <div class="amount-role-breakdown" v-if="scoreBreakdownEntries(candidate).length > 0">
                <p>分数拆解</p>
                <ul>
                  <li v-for="item in scoreBreakdownEntries(candidate)" :key="`${idx}-${item.key}`">
                    <span>{{ scoreKeyLabel(item.key) }}</span>
                    <strong>+{{ item.value }}</strong>
                  </li>
                </ul>
              </div>
            </div>
          </section>

          <div class="field-grid">
            <div class="field-wrap field-wrap--full">
              <label>修正テンプレート</label>
              <div class="template-row">
                <button
                  v-for="tpl in correctionTemplates"
                  :key="tpl.template_key"
                  :class="['secondary', { 'tpl-matched': tpl.matched }]"
                  type="button"
                  :title="tpl.merchant_pattern ? `マッチパターン: ${tpl.merchant_pattern}` : ''"
                  @click="applyCorrectionTemplate(tpl)"
                >
                  <span v-if="tpl.matched" class="tpl-match-dot" title="店舗名に自動マッチ">●</span>
                  {{ tpl.label }}
                </button>
                <span v-if="correctionTemplates.length === 0" class="muted-hint">
                  テンプレートなし —
                  <a href="/correction-templates" target="_blank">管理画面で追加</a>
                </span>
              </div>
              <p class="hint">店舗名・税率をよく使うパターンでワンクリック補正できます。</p>
            </div>

            <div class="field-wrap" :class="{ 'field-wrap--critical': highlightedFields.merchant }">
              <label>店舗名（必須）</label>
              <input v-model="form.merchant_name" type="text" />
            </div>
            <div class="field-wrap" :class="{ 'field-wrap--critical': highlightedFields.amount }">
              <label>合計金額（必須）</label>
              <input v-model="form.total_amount" type="number" min="0" step="1" @blur="form.total_amount = normalizeMoneyInput(form.total_amount)" />
            </div>
            <div class="field-wrap" :class="{ 'field-wrap--critical': highlightedFields.taxRate }">
              <label>税率（必須）</label>
              <select v-model="form.tax_rate_label">
                <option value="unknown">unknown</option>
                <option value="8">8%</option>
                <option value="10">10%</option>
                <option value="mixed">mixed</option>
              </select>
            </div>
            <div class="field-wrap">
              <label>税額</label>
              <input v-model="form.tax_amount" type="number" min="0" step="1" :readonly="form.tax_rate_label === 'mixed'" @input="onTaxAmountInput" @blur="form.tax_amount = normalizeMoneyInput(form.tax_amount)" />
              <div class="tax-help-row">
                <p class="hint hint--tight">{{ form.tax_rate_label === 'mixed' ? 'mixed は下の税率別明細から税額合計を自動反映します。' : '合計金額と税率が確定すると自動試算します。手動修正後は上書きしません。' }}</p>
                <button v-if="form.tax_rate_label !== 'mixed'" class="mini-link-btn" type="button" @click="recalculateTaxAmount(true)">再計算</button>
              </div>
              <p class="hint hint--tight">現在の取整规则: {{ roundingModeLabel(currentTaxConfig.jpy_rounding_mode) }} / {{ taxLevelLabel(currentTaxConfig.tax_rounding_level) }}</p>
            </div>
            <div v-if="form.tax_rate_label === 'mixed'" class="field-wrap field-wrap--full">
              <label>税率別明細</label>
              <div class="mixed-tax-table">
                <div class="mixed-tax-row mixed-tax-row--header">
                  <span>税率</span>
                  <span>課税対象額</span>
                  <span>税額</span>
                  <span></span>
                </div>
                <div v-for="(line, index) in formTaxLines" :key="`tax-line-${index}`" class="mixed-tax-row">
                  <select v-model="line.tax_rate" @change="syncMixedTaxLines()">
                    <option value="8">8%</option>
                    <option value="10">10%</option>
                  </select>
                  <input v-model="line.taxable_amount" type="number" min="0" step="1" @input="syncMixedTaxLines()" @blur="line.taxable_amount = normalizeMoneyInput(line.taxable_amount); syncMixedTaxLines()" />
                  <input :value="line.tax_amount" type="number" min="0" step="1" readonly />
                  <button class="secondary danger-lite" type="button" @click="removeTaxLine(index)">削除</button>
                </div>
              </div>
              <div class="mixed-tax-actions">
                <button class="secondary" type="button" @click="addTaxLine">＋ 行追加</button>
                <span class="hint">税額合計: {{ form.tax_amount ? `${Number(form.tax_amount).toLocaleString()}円` : '0円' }}</span>
              </div>
            </div>
            <div class="field-wrap">
              <label>取引日</label>
              <input v-model="form.transaction_date" type="date" />
            </div>
            <div class="field-wrap">
              <label>登録番号</label>
              <input v-model="form.registration_number" type="text" />
            </div>
            <div class="field-wrap" :class="{ 'field-wrap--warning': highlightedFields.subject }">
              <label>勘定科目（必須）</label>
              <div class="subject-row">
                <select v-model="form.subject_id">
                  <option value="">選択してください</option>
                  <option v-for="s in context.subjects" :key="s.id" :value="String(s.id)">
                    {{ s.subject_code }} - {{ s.subject_name }}
                  </option>
                </select>
                <button class="secondary" type="button" @click="applyRecommendedSubject">推奨を適用</button>
              </div>
              <p class="hint">推奨科目: {{ context.classification.subject_id ?? 'なし' }} / 分類信頼度: {{ context.classification.confidence ?? '-' }}</p>
            </div>
            <div class="field-wrap field-wrap--full">
              <label>判断メモ（差戻し時は必須）</label>
              <textarea v-model="form.note" rows="3" placeholder="何を確認し、何を修正したかを記録"></textarea>
            </div>
          </div>

          <p class="required-note" v-if="requiredMissing.length > 0">未入力の必須項目: {{ requiredMissing.join(' / ') }}</p>

          <div class="action-row">
            <button :disabled="!canApprove || loadingContext" @click="approveCurrent">修正して承認</button>
            <button class="secondary" :disabled="loadingContext" @click="requestCorrection">差戻し（修正後承認）</button>
          </div>

          <div class="posting-preview">
            <p class="panel-title">承認後の仕訳プレビュー</p>
            <div class="posting-grid">
              <p><strong>取引日</strong> {{ postingPreview.entryDate }}</p>
              <p><strong>借方科目</strong> {{ postingPreview.debitSubject }}</p>
              <p><strong>金額</strong> {{ postingPreview.amount }}</p>
              <p><strong>税率</strong> {{ postingPreview.taxRate }}</p>
              <p><strong>店舗名</strong> {{ postingPreview.merchant }}</p>
              <p><strong>登録番号</strong> {{ postingPreview.registration }}</p>
              <p class="posting-note"><strong>備考</strong> {{ postingPreview.note }}</p>
            </div>
          </div>

          <div class="audit-panel">
            <p class="panel-title">監査ログ</p>
            <p v-if="context.audit_logs.length === 0" class="preview-empty">監査ログはまだありません。</p>
            <div v-for="log in context.audit_logs" :key="log.id" class="audit-item">
              <p class="audit-head">{{ log.created_at }} / {{ log.action_type }} / 担当: {{ log.changed_by ?? '-' }}</p>
              <p class="audit-note">理由: {{ log.reason_note || '-' }}</p>
              <div class="diff-table" v-if="getAuditDiffRows(log).length > 0">
                <div class="diff-head">変更フィールド</div>
                <div class="diff-row diff-row--header">
                  <span>項目</span>
                  <span>変更前</span>
                  <span>変更後</span>
                </div>
                <div class="diff-row" v-for="row in getAuditDiffRows(log)" :key="`${log.id}-${row.key}`">
                  <span>{{ row.label }}</span>
                  <span class="diff-before">{{ row.before }}</span>
                  <span class="diff-after">{{ row.after }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div v-else class="work-panel-empty">
        <p>レビュー待ちタスクはありません。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.action-message {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #c4dbf0;
  background: #edf6ff;
  color: #1f4f82;
  font-size: 13px;
}

.success-toast {
  position: fixed;
  top: 18px;
  right: 20px;
  z-index: 1300;
  min-width: 280px;
  max-width: min(520px, calc(100vw - 28px));
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #72c98a;
  background: linear-gradient(135deg, #eaffef 0%, #d8f9e4 100%);
  color: #0f5a2a;
  box-shadow: 0 12px 30px rgba(32, 99, 55, 0.18);
  font-size: 13px;
  font-weight: 600;
}

.review-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 12px;
  align-items: start;
}

.task-list-panel {
  max-height: calc(100dvh - 180px);
  overflow-y: auto;
}

.task-list-panel,
.pane {
  border: 1px solid #d3ddec;
  border-radius: 12px;
  background: #fbfdff;
  padding: 10px;
}

.task-list-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.queue-count {
  font-size: 11px;
  font-weight: 400;
  color: #6b7e95;
  margin-left: 6px;
}

.queue-nav {
  display: flex;
  gap: 4px;
}

.nav-btn {
  background: #f0f4fa;
  border: 1px solid #d3ddec;
  border-radius: 5px;
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
  color: #374151;
  line-height: 1.6;
}

.nav-btn:hover:not(:disabled) { background: #dfeaf7; }
.nav-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.queue-search {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 7px;
  font-size: 12px;
  margin-bottom: 8px;
  color: #111827;
}

.queue-search:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 2px #bfdbfe;
}

.queue-filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.filter-chip {
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  padding: 2px 9px;
  font-size: 11px;
  cursor: pointer;
  color: #374151;
}

.filter-chip:hover { background: #e5e7eb; }

.filter-chip--active {
  background: #1e40af;
  color: #fff;
  border-color: #1e40af;
}

.filter-chip--critical.filter-chip--active { background: #991b1b; border-color: #991b1b; }
.filter-chip--warning.filter-chip--active  { background: #92400e; border-color: #92400e; }

.queue-empty {
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
  padding: 16px 0;
}

.task-card-list {
  flex: 1;
  overflow-y: auto;
}

.task-card {
  width: 100%;
  text-align: left;
  margin-bottom: 8px;
  padding: 8px;
  border-radius: 10px;
  border: 1px solid #d3deed;
  background: #fff;
  color: #273b57;
  cursor: pointer;
}

.task-card--active {
  border-color: #5e86b8;
  box-shadow: inset 0 0 0 1px #5e86b8;
}

.task-card--critical {
  border-left: 3px solid #dc2626;
}

.task-card-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 4px;
  margin-bottom: 2px;
}

.task-merchant {
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 70%;
}

.task-amount {
  font-size: 11px;
  color: #1e3a5f;
  white-space: nowrap;
  font-weight: 600;
}

.task-id {
  margin: 0 0 6px;
  font-size: 11px;
  color: #6b7e95;
  font-weight: 400;
}

.reason-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.reason-list--inline {
  margin-bottom: 10px;
}

.reason-pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.reason-pill--warning {
  background: #fff3dd;
  color: #8a5704;
}

.reason-pill--critical {
  background: #ffe7e7;
  color: #992929;
}

.work-panel {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) 1.1fr;
  gap: 12px;
}

.doc-meta {
  margin: 0 0 8px;
  font-size: 12px;
  color: #51657f;
}

.preview-image-stage {
  width: 100%;
  max-height: 65dvh;
  min-height: 320px;
  overflow: hidden;
  object-fit: contain;
  border: 1px solid #d8e0ec;
  border-radius: 8px;
  background: #fff;
}

.preview-image {
  width: 100%;
  height: 100%;
  max-height: 65dvh;
  object-fit: contain;
  user-select: none;
  transform-origin: center center;
}

.preview-image--grabbable {
  cursor: grab;
}

.preview-image--grabbing {
  cursor: grabbing;
}

.preview-zoom-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: #4a617f;
}

.preview-zoom-toolbar button {
  width: auto;
  min-width: 36px;
  padding: 4px 10px;
}

.zoom-hint {
  color: #6b7e95;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.preview-pdf {
  width: 100%;
  height: 65dvh;
  border: 1px solid #d8e0ec;
  border-radius: 8px;
  background: #fff;
}

.preview-empty {
  margin: 0;
  font-size: 12px;
  color: #6b7e95;
}

.thumbnail-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.thumbnail-card {
  border: 1px solid #d8e0ec;
  border-radius: 8px;
  background: #fff;
  padding: 4px;
  cursor: pointer;
  text-align: left;
}

.thumbnail-card--active {
  border-color: #2563eb;
  box-shadow: inset 0 0 0 1px #2563eb;
}

.thumbnail-media {
  aspect-ratio: 1 / 1;
  border-radius: 6px;
  overflow: hidden;
  background: #f3f6fb;
  display: flex;
  align-items: center;
  justify-content: center;
}

.thumbnail-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumbnail-placeholder {
  font-size: 11px;
  font-weight: 700;
  color: #60758f;
}

.thumbnail-label {
  display: block;
  margin-top: 4px;
  font-size: 10px;
  line-height: 1.3;
  color: #304763;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.amount-role-panel {
  border: 1px solid #d6dfec;
  border-radius: 10px;
  padding: 10px;
  background: #f8fbff;
  margin-bottom: 10px;
}

.amount-role-head {
  margin-bottom: 8px;
}

.amount-role-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #5d7390;
}

.amount-role-selected {
  margin-bottom: 8px;
  padding: 8px;
  border-radius: 8px;
  background: #eaf3ff;
  border: 1px solid #c7dbf3;
  font-size: 12px;
  color: #1f3f64;
}

.amount-role-card {
  border: 1px solid #d6dfec;
  border-radius: 8px;
  background: #fff;
  padding: 8px;
  margin-bottom: 8px;
}

.amount-role-summary p {
  margin: 0 0 4px;
  font-size: 12px;
  color: #334a65;
}

.amount-role-lines {
  color: #6a7f99 !important;
}

.amount-role-breakdown p {
  margin: 6px 0 4px;
  font-size: 12px;
  font-weight: 700;
  color: #2b4361;
}

.amount-role-breakdown ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.amount-role-breakdown li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: #4a627d;
  padding: 2px 0;
}

.amount-role-breakdown li strong {
  color: #1f4f82;
}

.field-wrap {
  border: 1px solid #d6dfec;
  border-radius: 8px;
  padding: 8px;
  background: #fff;
}

.field-wrap label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 700;
  color: #344a66;
}

.field-wrap--full {
  grid-column: 1 / -1;
}

.field-wrap--critical {
  border-color: #efb7b7;
  background: #fff7f7;
}

.field-wrap--warning {
  border-color: #f0d2a7;
  background: #fffaf0;
}

.template-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.template-row button {
  width: auto;
}

.tpl-matched {
  border-color: #16a34a !important;
  background: #f0fdf4 !important;
  color: #15803d !important;
}

.tpl-match-dot {
  color: #16a34a;
  font-size: 8px;
  vertical-align: middle;
  margin-right: 2px;
}

.muted-hint {
  font-size: 11px;
  color: #9ca3af;
  align-self: center;
}

.muted-hint a {
  color: #2563eb;
  text-decoration: underline;
}

.subject-row {
  display: flex;
  gap: 8px;
}

.subject-row button {
  width: auto;
}

.hint {
  margin: 6px 0 0;
  font-size: 11px;
  color: #60758f;
}

.hint--tight {
  margin-top: 4px;
}

.tax-help-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.mixed-tax-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mixed-tax-row {
  display: grid;
  grid-template-columns: 90px 1fr 1fr 72px;
  gap: 8px;
  align-items: center;
}

.mixed-tax-row--header {
  font-size: 11px;
  font-weight: 700;
  color: #60758f;
}

.mixed-tax-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.danger-lite {
  color: #b91c1c;
}

.mini-link-btn {
  border: 0;
  background: transparent;
  color: #2563eb;
  font-size: 11px;
  cursor: pointer;
  padding: 0;
  white-space: nowrap;
}

.mini-link-btn:hover {
  text-decoration: underline;
}

.required-note {
  margin: 10px 0 0;
  color: #9c2d2d;
  font-size: 12px;
}

.action-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.posting-preview {
  margin-top: 12px;
  border-top: 1px solid #dbe3ef;
  padding-top: 10px;
}

.posting-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 12px;
}

.posting-grid p {
  margin: 0;
  font-size: 12px;
  color: #324a67;
}

.posting-note {
  grid-column: 1 / -1;
}

.audit-panel {
  margin-top: 12px;
  border-top: 1px solid #dbe3ef;
  padding-top: 10px;
}

.audit-item {
  border: 1px solid #dbe3ef;
  background: #f8fbff;
  border-radius: 8px;
  padding: 8px;
  margin-bottom: 7px;
}

.audit-head,
.audit-note {
  margin: 0;
  font-size: 12px;
  color: #304763;
}

.diff-table {
  margin-top: 8px;
  border: 1px solid #d6e0ee;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.diff-head {
  padding: 6px 8px;
  font-size: 11px;
  font-weight: 700;
  color: #415872;
  background: #f3f7fd;
  border-bottom: 1px solid #d6e0ee;
}

.diff-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px;
  padding: 6px 8px;
  border-top: 1px solid #edf2f8;
  font-size: 12px;
}

.diff-row--header {
  background: #f8fbff;
  font-weight: 700;
  border-top: 0;
}

.diff-before {
  color: #855f07;
}

.diff-after {
  color: #1f6f41;
}

.work-panel-empty {
  border: 1px dashed #c7d4e6;
  border-radius: 12px;
  padding: 16px;
  color: #5a6f88;
}

@media (max-width: 900px) {
  .success-toast {
    top: auto;
    right: 12px;
    left: 12px;
    bottom: 12px;
    min-width: 0;
    max-width: none;
  }

  .review-layout,
  .work-panel {
    grid-template-columns: 1fr;
  }

  .field-grid {
    grid-template-columns: 1fr;
  }

  .thumbnail-strip {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .preview-image-stage {
    min-height: 220px;
  }

  .preview-zoom-toolbar {
    flex-wrap: wrap;
  }

  .mixed-tax-row {
    grid-template-columns: 1fr;
  }

  .posting-grid {
    grid-template-columns: 1fr;
  }

  .diff-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .subject-row {
    flex-direction: column;
  }

  .action-row {
    flex-direction: column;
  }
}
</style>