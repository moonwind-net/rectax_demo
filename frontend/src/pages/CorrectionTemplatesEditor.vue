<template>
  <div class="page">
    <div class="page-header">
      <h2>修正テンプレート管理</h2>
      <button class="btn-primary" @click="openCreate">＋ 新規テンプレート</button>
    </div>

    <p class="hint-text">
      店舗名に正規表現でマッチするテンプレートは、レビュー画面で自動提案されます。
    </p>

    <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>
    <p v-if="successMsg" class="success-msg">{{ successMsg }}</p>

    <div v-if="loading" class="loading-text">読み込み中...</div>

    <div v-else-if="templates.length === 0" class="empty-state">
      テンプレートがまだありません。「＋ 新規テンプレート」から追加してください。
    </div>

    <table v-else class="tpl-table">
      <thead>
        <tr>
          <th>優先度</th>
          <th>キー</th>
          <th>ラベル</th>
          <th>店舗マッチパターン</th>
          <th>適用フィールド</th>
          <th>メモプレフィックス</th>
          <th>状態</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="tpl in templates" :key="tpl.id" :class="{ inactive: !tpl.is_active }">
          <td class="center">{{ tpl.priority }}</td>
          <td class="mono">{{ tpl.template_key }}</td>
          <td>{{ tpl.label }}</td>
          <td class="mono">{{ tpl.merchant_pattern || "—" }}</td>
          <td>
            <span v-for="(v, k) in tpl.patch_fields" :key="k" class="field-chip">
              {{ k }}: {{ v }}
            </span>
            <span v-if="Object.keys(tpl.patch_fields).length === 0" class="muted">—</span>
          </td>
          <td class="muted-cell">{{ tpl.note_prefix || "—" }}</td>
          <td>
            <span :class="tpl.is_active ? 'badge-active' : 'badge-inactive'">
              {{ tpl.is_active ? "有効" : "無効" }}
            </span>
          </td>
          <td class="actions">
            <button class="btn-sm" @click="openEdit(tpl)">編集</button>
            <button class="btn-sm danger" @click="deleteTemplate(tpl)">削除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Modal -->
    <div v-if="modal.open" class="modal-overlay" @click.self="closeModal">
      <div class="modal">
        <h3>{{ modal.isEdit ? "テンプレート編集" : "テンプレート新規作成" }}</h3>

        <div v-if="modal.formError" class="form-error">{{ modal.formError }}</div>

        <template v-if="!modal.isEdit">
          <label>テンプレートキー（英数字/_-）</label>
          <input v-model="modal.form.template_key" placeholder="supermarket-10" />
        </template>

        <label>ラベル（表示名）</label>
        <input v-model="modal.form.label" placeholder="スーパー（標準10%）" />

        <label>
          店舗名マッチパターン（正規表現、省略可）
          <span class="hint">例: <code>セブン|ローソン|ファミマ</code> → コンビニ全般にマッチ</span>
        </label>
        <input v-model="modal.form.merchant_pattern" placeholder="セブン.*|ローソン.*" />

        <label>適用フィールド（JSON）</label>
        <textarea
          v-model="modal.patchFieldsRaw"
          rows="4"
          placeholder='{"tax_rate_label": "10%"}'
          class="mono"
        ></textarea>

        <label>メモプレフィックス（省略可）</label>
        <input v-model="modal.form.note_prefix" placeholder="テンプレ適用: スーパー標準税率" />

        <label>優先度（数値が大きいほど上位に表示）</label>
        <input v-model.number="modal.form.priority" type="number" min="0" max="9999" />

        <label class="checkbox-label">
          <input type="checkbox" v-model="modal.form.is_active" />
          有効（レビュー画面に表示する）
        </label>

        <div class="pf-preview" v-if="parsedPatchFields !== null">
          <span class="preview-label">プレビュー：</span>
          <span v-for="(v, k) in parsedPatchFields" :key="k" class="field-chip">{{ k }}: {{ v }}</span>
        </div>
        <div v-else-if="modal.patchFieldsRaw.trim()" class="pf-error">JSON解析エラー — 記法を確認してください</div>

        <div class="modal-actions">
          <button class="btn-primary" :disabled="saving" @click="saveTemplate">
            {{ saving ? "保存中..." : "保存" }}
          </button>
          <button class="btn-sm" @click="closeModal">キャンセル</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../services/api";

interface CorrectionTemplate {
  id: number;
  template_key: string;
  label: string;
  merchant_pattern: string | null;
  patch_fields: Record<string, string>;
  note_prefix: string | null;
  priority: number;
  is_active: boolean;
}

interface ModalForm {
  template_key: string;
  label: string;
  merchant_pattern: string;
  note_prefix: string;
  priority: number;
  is_active: boolean;
}

const templates = ref<CorrectionTemplate[]>([]);
const loading = ref(false);
const saving = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

const modal = reactive({
  open: false,
  isEdit: false,
  editId: 0,
  form: {
    template_key: "",
    label: "",
    merchant_pattern: "",
    note_prefix: "",
    priority: 0,
    is_active: true,
  } as ModalForm,
  patchFieldsRaw: "{}",
  formError: "",
});

const parsedPatchFields = computed<Record<string, string> | null>(() => {
  try {
    const parsed = JSON.parse(modal.patchFieldsRaw);
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
});

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    const res = await api.get<CorrectionTemplate[]>("/correction-templates");
    templates.value = res.data;
  } catch {
    errorMsg.value = "テンプレートの読み込みに失敗しました";
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  modal.open = true;
  modal.isEdit = false;
  modal.editId = 0;
  modal.formError = "";
  modal.patchFieldsRaw = "{}";
  modal.form = {
    template_key: "",
    label: "",
    merchant_pattern: "",
    note_prefix: "",
    priority: 0,
    is_active: true,
  };
}

function openEdit(tpl: CorrectionTemplate) {
  modal.open = true;
  modal.isEdit = true;
  modal.editId = tpl.id;
  modal.formError = "";
  modal.patchFieldsRaw = JSON.stringify(tpl.patch_fields, null, 2);
  modal.form = {
    template_key: tpl.template_key,
    label: tpl.label,
    merchant_pattern: tpl.merchant_pattern ?? "",
    note_prefix: tpl.note_prefix ?? "",
    priority: tpl.priority,
    is_active: tpl.is_active,
  };
}

function closeModal() {
  modal.open = false;
}

function flashSuccess(msg: string) {
  successMsg.value = msg;
  setTimeout(() => (successMsg.value = ""), 3000);
}

async function saveTemplate() {
  modal.formError = "";
  if (!modal.form.label.trim()) {
    modal.formError = "ラベルは必須です";
    return;
  }
  if (parsedPatchFields.value === null) {
    modal.formError = "「適用フィールド」のJSONが不正です";
    return;
  }
  saving.value = true;
  try {
    const payload: Record<string, unknown> = {
      label: modal.form.label.trim(),
      merchant_pattern: modal.form.merchant_pattern.trim() || null,
      patch_fields: parsedPatchFields.value,
      note_prefix: modal.form.note_prefix.trim() || null,
      priority: modal.form.priority,
      is_active: modal.form.is_active,
    };
    if (!modal.isEdit) {
      if (!modal.form.template_key.trim()) {
        modal.formError = "テンプレートキーは必須です";
        return;
      }
      payload.template_key = modal.form.template_key.trim();
      await api.post("/correction-templates", payload);
      flashSuccess("テンプレートを作成しました");
    } else {
      await api.put(`/correction-templates/${modal.editId}`, payload);
      flashSuccess("テンプレートを更新しました");
    }
    modal.open = false;
    await load();
  } catch (err: any) {
    const detail = err?.response?.data?.detail;
    modal.formError = typeof detail === "string" ? detail : "保存に失敗しました";
  } finally {
    saving.value = false;
  }
}

async function deleteTemplate(tpl: CorrectionTemplate) {
  if (!confirm(`「${tpl.label}」を削除しますか？この操作は元に戻せません。`)) return;
  try {
    await api.delete(`/correction-templates/${tpl.id}`);
    flashSuccess("削除しました");
    await load();
  } catch {
    errorMsg.value = "削除に失敗しました";
  }
}

onMounted(load);
</script>

<style scoped>
.page {
  padding: 24px;
  max-width: 1100px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.page-header h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
}
.hint-text {
  font-size: 0.85rem;
  color: #6b7280;
  margin: 0 0 16px;
}
.error-msg {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 0.875rem;
}
.success-msg {
  color: #16a34a;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 0.875rem;
}
.loading-text {
  color: #6b7280;
  padding: 24px 0;
  text-align: center;
}
.empty-state {
  color: #6b7280;
  text-align: center;
  padding: 40px 0;
  font-size: 0.9rem;
}
.tpl-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.tpl-table th {
  text-align: left;
  padding: 8px 10px;
  background: #f9fafb;
  border-bottom: 2px solid #e5e7eb;
  font-weight: 600;
  color: #374151;
  white-space: nowrap;
}
.tpl-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f3f4f6;
  vertical-align: middle;
}
.tpl-table tr.inactive td {
  opacity: 0.5;
}
.tpl-table tr:hover td {
  background: #f9fafb;
}
.center { text-align: center; }
.mono { font-family: monospace; font-size: 0.8rem; }
.muted { color: #9ca3af; }
.muted-cell { color: #9ca3af; font-size: 0.8rem; }
.field-chip {
  display: inline-block;
  background: #eff6ff;
  color: #1d4ed8;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 0.75rem;
  margin-right: 4px;
  font-family: monospace;
}
.badge-active {
  background: #dcfce7;
  color: #16a34a;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 0.75rem;
  font-weight: 600;
}
.badge-inactive {
  background: #f3f4f6;
  color: #9ca3af;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 0.75rem;
  font-weight: 600;
}
.actions { white-space: nowrap; }
.btn-primary {
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 8px 16px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover { background: #1d4ed8; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-sm {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #e5e7eb;
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 0.8rem;
  cursor: pointer;
  margin-right: 4px;
}
.btn-sm:hover { background: #e5e7eb; }
.btn-sm.danger { color: #dc2626; }
.btn-sm.danger:hover { background: #fee2e2; border-color: #fca5a5; }

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  background: #fff;
  border-radius: 10px;
  padding: 28px 32px;
  width: 540px;
  max-width: 95vw;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.modal h3 {
  margin: 0 0 8px;
  font-size: 1.1rem;
  font-weight: 700;
}
.modal label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #374151;
  margin-top: 6px;
}
.modal label .hint {
  font-weight: 400;
  color: #9ca3af;
  margin-left: 6px;
}
.modal input,
.modal textarea,
.modal select {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.875rem;
  color: #111827;
}
.modal input:focus,
.modal textarea:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 2px #bfdbfe;
}
.modal textarea.mono { font-family: monospace; font-size: 0.8rem; }
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  cursor: pointer;
}
.checkbox-label input[type="checkbox"] {
  width: auto;
  cursor: pointer;
}
.pf-preview {
  background: #eff6ff;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.8rem;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.preview-label {
  color: #6b7280;
  font-size: 0.75rem;
  margin-right: 4px;
}
.pf-error {
  color: #dc2626;
  font-size: 0.8rem;
}
.form-error {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.8rem;
}
.modal-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
