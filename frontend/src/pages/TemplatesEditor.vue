<template>
  <div class="page">
    <div class="page-header">
      <h2>仕訳エクスポートテンプレート管理</h2>
      <button class="btn-primary" @click="openCreate">＋ 新規テンプレート</button>
    </div>

    <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

    <div class="cards">
      <div v-for="tmpl in templates" :key="tmpl.id" class="card">
        <div class="card-header">
          <div>
            <strong>{{ tmpl.template_code }}</strong>
            <span v-if="tmpl.is_default" class="default-badge">デフォルト</span>
          </div>
          <div class="card-actions">
            <button class="btn-sm" @click="openEdit(tmpl)">編集</button>
            <button class="btn-sm danger" @click="deleteTemplate(tmpl.id)">削除</button>
          </div>
        </div>
        <p class="card-name">{{ tmpl.template_name }}</p>
        <p class="card-meta">エンコード: {{ tmpl.encoding }} | 区切り: {{ tmpl.delimiter === "," ? "カンマ" : "タブ" }}</p>
        <div class="col-chips">
          <span v-for="col in tmpl.columns" :key="col.header" class="chip">
            {{ col.header }} → {{ col.field }}
          </span>
        </div>
      </div>
    </div>

    <!-- Modal -->
    <div v-if="modal.open" class="modal-overlay" @click.self="modal.open = false">
      <div class="modal">
        <h3>{{ modal.isEdit ? "テンプレート編集" : "テンプレート新規作成" }}</h3>

        <template v-if="!modal.isEdit">
          <label>テンプレートコード（英数字/_-）</label>
          <input v-model="modal.form.template_code" placeholder="JP_STD_A" />
        </template>

        <label>テンプレート名</label>
        <input v-model="modal.form.template_name" placeholder="標準仕訳フォーマットA" />

        <label>エンコード</label>
        <select v-model="modal.form.encoding">
          <option value="UTF-8">UTF-8</option>
          <option value="Shift_JIS">Shift_JIS（会計ソフト）</option>
          <option value="CP932">CP932（Windows）</option>
        </select>

        <label>区切り文字</label>
        <select v-model="modal.form.delimiter">
          <option value=",">カンマ (CSV)</option>
          <option value="	">タブ (TSV)</option>
        </select>

        <label>カラム定義</label>
        <div class="col-editor">
          <div class="col-row col-header-row">
            <span>ヘッダー名</span><span>フィールドキー</span><span></span>
          </div>
          <div v-for="(col, idx) in modal.columns" :key="idx" class="col-row">
            <input v-model="col.header" placeholder="取引日" />
            <select v-model="col.field">
              <option v-for="f in AVAILABLE_FIELDS" :key="f.key" :value="f.key">{{ f.label }}</option>
            </select>
            <button class="btn-sm danger" @click="removeCol(idx)">✕</button>
          </div>
          <button class="btn-sm" @click="addCol">＋ カラム追加</button>
        </div>

        <label class="checkbox-label">
          <input type="checkbox" v-model="modal.form.is_default" />
          デフォルトテンプレートとして設定
        </label>

        <div class="modal-actions">
          <button class="btn-primary" @click="saveTemplate">保存</button>
          <button class="btn-sm" @click="modal.open = false">キャンセル</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { api } from "../services/api";

const AVAILABLE_FIELDS = [
  { key: "transaction_date", label: "取引日" },
  { key: "merchant_name", label: "店舗名" },
  { key: "registration_number", label: "インボイス登録番号" },
  { key: "subtotal_excl_tax", label: "税抜小計" },
  { key: "tax_amount", label: "消費税額" },
  { key: "total_amount", label: "合計金額" },
  { key: "tax_rate_label", label: "消費税区分" },
  { key: "payment_method", label: "支払方法" },
  { key: "subject_code", label: "勘定科目コード" },
  { key: "subject_name", label: "勘定科目名" },
  { key: "original_filename", label: "ファイル名" },
  { key: "document_status", label: "ステータス" },
];

interface ColItem { header: string; field: string; }
interface Template {
  id: number;
  template_code: string;
  template_name: string;
  encoding: string;
  delimiter: string;
  columns: ColItem[];
  is_default: boolean;
}

const templates = ref<Template[]>([]);
const errorMsg = ref("");

const emptyForm = () => ({
  template_code: "",
  template_name: "",
  encoding: "UTF-8",
  delimiter: ",",
  is_default: false,
});

const modal = reactive({
  open: false,
  isEdit: false,
  editId: 0,
  form: emptyForm(),
  columns: [] as ColItem[],
});

onMounted(loadTemplates);

async function loadTemplates() {
  try {
    const res = await api.get("/exports/templates");
    templates.value = res.data;
  } catch {
    errorMsg.value = "テンプレートの読み込みに失敗しました";
  }
}

function openCreate() {
  modal.isEdit = false;
  modal.editId = 0;
  modal.form = emptyForm();
  modal.columns = [{ header: "", field: "transaction_date" }];
  modal.open = true;
}

function openEdit(tmpl: Template) {
  modal.isEdit = true;
  modal.editId = tmpl.id;
  modal.form = { ...tmpl };
  modal.columns = tmpl.columns.map((c) => ({ ...c }));
  modal.open = true;
}

function addCol() {
  modal.columns.push({ header: "", field: "transaction_date" });
}
function removeCol(idx: number) {
  modal.columns.splice(idx, 1);
}

async function saveTemplate() {
  const payload = { ...modal.form, columns: modal.columns };
  try {
    if (modal.isEdit) {
      await api.put(`/exports/templates/${modal.editId}`, { ...payload });
    } else {
      await api.post("/exports/templates", payload);
    }
    modal.open = false;
    await loadTemplates();
  } catch (err: any) {
    errorMsg.value = err.response?.data?.detail ?? "保存に失敗しました";
  }
}

async function deleteTemplate(id: number) {
  if (!confirm("このテンプレートを削除しますか？")) return;
  await api.delete(`/exports/templates/${id}`);
  await loadTemplates();
}
</script>

<style scoped>
.page { padding: 1.5rem; max-width: 1000px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; }
.page-header h2 { margin: 0; }
.error-msg { color: #c0392b; font-size: 0.875rem; }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; }
.card { background: #fff; border-radius: 10px; padding: 1rem 1.25rem; box-shadow: 0 1px 6px rgba(0,0,0,0.07); }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.4rem; }
.card-name { margin: 0.2rem 0; color: #444; font-size: 0.92rem; }
.card-meta { font-size: 0.8rem; color: #888; margin: 0.25rem 0 0.5rem; }
.default-badge { background: #d4edda; color: #155724; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; margin-left: 0.5rem; }
.col-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.chip { background: #eef; font-size: 0.76rem; padding: 2px 8px; border-radius: 10px; color: #334; }

.card-actions { display: flex; gap: 0.4rem; }
.btn-sm { padding: 0.3rem 0.7rem; font-size: 0.82rem; border: 1px solid #ccc; border-radius: 5px; cursor: pointer; background: #fff; }
.btn-sm.danger { border-color: #e74c3c; color: #e74c3c; }
.btn-primary { background: #1a2340; color: #fff; border: none; border-radius: 8px; padding: 0.55rem 1.2rem; font-size: 0.92rem; cursor: pointer; font-weight: 600; }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: #fff; padding: 1.5rem; border-radius: 12px; width: 560px; max-height: 90vh; overflow-y: auto; }
.modal h3 { margin: 0 0 1rem; }
.modal label { display: block; font-size: 0.83rem; font-weight: 600; color: #444; margin-top: 0.75rem; margin-bottom: 0.3rem; }
.modal input, .modal select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.7rem; border: 1px solid #ccc; border-radius: 6px; font-size: 0.9rem; }
.checkbox-label { display: flex; align-items: center; gap: 0.5rem; margin-top: 1rem; font-size: 0.9rem; }
.checkbox-label input { width: auto; }

.col-editor { border: 1px solid #e0e0e0; border-radius: 8px; padding: 0.75rem; margin-top: 0.4rem; }
.col-row { display: grid; grid-template-columns: 1fr 1fr 40px; gap: 0.5rem; align-items: center; margin-bottom: 0.4rem; }
.col-header-row { font-size: 0.78rem; font-weight: 600; color: #888; margin-bottom: 0.5rem; }
.col-row input, .col-row select { width: 100%; }

.modal-actions { display: flex; gap: 0.75rem; margin-top: 1.25rem; }

@media (max-width: 900px) {
  .page {
    padding: 0.9rem;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 0.7rem;
  }

  .cards {
    grid-template-columns: 1fr;
  }

  .card {
    padding: 0.9rem;
  }

  .card-header {
    flex-direction: column;
    gap: 0.5rem;
  }

  .card-actions {
    width: 100%;
  }

  .card-actions .btn-sm {
    width: 100%;
  }

  .modal {
    width: calc(100vw - 1.2rem);
    padding: 1rem;
  }

  .col-row {
    grid-template-columns: 1fr;
  }

  .col-header-row {
    display: none;
  }

  .modal-actions {
    flex-direction: column;
  }

  .modal-actions .btn-sm,
  .modal-actions .btn-primary {
    width: 100%;
  }
}
</style>
