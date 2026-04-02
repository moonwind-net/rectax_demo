<template>
  <div class="page">
    <div class="page-header">
      <h2>勘定科目判定ルール管理</h2>
      <button class="btn-primary" @click="openCreateModal">＋ 新規ルール</button>
    </div>

    <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>優先度</th>
            <th>ルール名</th>
            <th>判定種別</th>
            <th>判定条件</th>
            <th>勘定科目</th>
            <th>信頼度</th>
            <th>状態</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(rule, idx) in rules" :key="rule.id">
            <td class="priority-cell">
              <input
                type="number"
                v-model.number="rule.priority"
                min="1" max="9999"
                style="width:60px"
                @change="onPriorityChange(idx)"
              />
            </td>
            <td>{{ rule.rule_name }}</td>
            <td><span class="badge">{{ rule.rule_type }}</span></td>
            <td class="cond-cell"><pre>{{ JSON.stringify(rule.rule_condition, null, 2) }}</pre></td>
            <td>{{ subjectName(rule.target_subject_id) }}</td>
            <td>{{ (rule.score * 100).toFixed(0) }}%</td>
            <td>
              <span :class="rule.is_active ? 'active-dot' : 'inactive-dot'">
                {{ rule.is_active ? "有効" : "無効" }}
              </span>
            </td>
            <td class="action-cell">
              <button class="btn-sm" @click="openEditModal(rule)">編集</button>
              <button class="btn-sm danger" @click="toggleActive(rule)">
                {{ rule.is_active ? "無効化" : "有効化" }}
              </button>
              <button class="btn-sm danger" @click="deleteRule(rule.id)">削除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <button v-if="dirtyPriorities.size > 0" class="btn-primary save-order-btn" @click="saveOrder">
      優先度を反映 ({{ dirtyPriorities.size }}件)
    </button>

    <!-- Modal -->
    <div v-if="modal.open" class="modal-overlay" @click.self="modal.open = false">
      <div class="modal">
        <h3>{{ modal.isEdit ? "ルール編集" : "ルール新規作成" }}</h3>
        <label>ルール名</label>
        <input v-model="modal.form.rule_name" placeholder="例: コンビニ → 消耗品費" />

        <label>判定種別</label>
        <select v-model="modal.form.rule_type">
          <option value="keyword">キーワード</option>
          <option value="merchant_exact">店舗名完全一致</option>
          <option value="amount_range">金額範囲</option>
          <option value="regex">正規表現</option>
        </select>

        <label>判定条件 JSON</label>
        <textarea v-model="modal.conditionRaw" rows="5" placeholder='{"keyword": "コンビニ"}' />
        <p v-if="modal.conditionError" class="error-msg">{{ modal.conditionError }}</p>

        <label>勘定科目</label>
        <select v-model.number="modal.form.target_subject_id">
          <option v-for="s in subjects" :key="s.id" :value="s.id">
            {{ s.subject_code }} {{ s.subject_name }}
          </option>
        </select>

        <label>優先度</label>
        <input type="number" v-model.number="modal.form.priority" min="1" max="9999" />

        <label>スコア (0–1)</label>
        <input type="number" v-model.number="modal.form.score" min="0" max="1" step="0.01" />

        <div class="modal-actions">
          <button class="btn-primary" @click="saveRule">保存</button>
          <button class="btn-sm" @click="modal.open = false">キャンセル</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { api } from "../services/api";

interface Rule {
  id: number;
  rule_name: string;
  rule_type: string;
  rule_condition: object;
  target_subject_id: number;
  priority: number;
  score: number;
  is_active: boolean;
}
interface Subject { id: number; subject_code: string; subject_name: string; }

const rules = ref<Rule[]>([]);
const subjects = ref<Subject[]>([]);
const errorMsg = ref("");
const dirtyPriorities = ref(new Set<number>());

const emptyForm = () => ({
  rule_name: "",
  rule_type: "keyword",
  target_subject_id: 0,
  priority: 100,
  score: 1.0,
});

const modal = reactive({
  open: false,
  isEdit: false,
  editId: 0,
  form: emptyForm(),
  conditionRaw: "{}",
  conditionError: "",
});

onMounted(async () => {
  await Promise.all([loadRules(), loadSubjects()]);
});

async function loadRules() {
  try {
    const res = await api.get("/categories/rules");
    rules.value = res.data;
  } catch {
    errorMsg.value = "判定ルールの読み込みに失敗しました";
  }
}
async function loadSubjects() {
  const res = await api.get("/categories/subjects");
  subjects.value = res.data;
}

function subjectName(id: number): string {
  const s = subjects.value.find((x) => x.id === id);
  return s ? `${s.subject_code} ${s.subject_name}` : String(id);
}

function openCreateModal() {
  modal.isEdit = false;
  modal.editId = 0;
  modal.form = emptyForm();
  modal.conditionRaw = "{}";
  modal.conditionError = "";
  modal.open = true;
}
function openEditModal(rule: Rule) {
  modal.isEdit = true;
  modal.editId = rule.id;
  modal.form = { ...rule };
  modal.conditionRaw = JSON.stringify(rule.rule_condition, null, 2);
  modal.conditionError = "";
  modal.open = true;
}

function onPriorityChange(idx: number) {
  dirtyPriorities.value.add(rules.value[idx].id);
}

async function saveOrder() {
  const items = [...dirtyPriorities.value].map((id) => {
    const r = rules.value.find((x) => x.id === id)!;
    return { id, priority: r.priority };
  });
  await api.patch("/categories/rules/reorder", items);
  dirtyPriorities.value.clear();
}

async function saveRule() {
  try {
    modal.form.rule_condition = JSON.parse(modal.conditionRaw);
    modal.conditionError = "";
  } catch {
    modal.conditionError = "判定条件JSONが無効です";
    return;
  }
  try {
    if (modal.isEdit) {
      await api.put(`/categories/rules/${modal.editId}`, modal.form);
    } else {
      await api.post("/categories/rules", modal.form);
    }
    modal.open = false;
    await loadRules();
  } catch (err: any) {
    errorMsg.value = err.response?.data?.detail ?? "保存に失敗しました";
  }
}

async function toggleActive(rule: Rule) {
  await api.put(`/categories/rules/${rule.id}`, { is_active: !rule.is_active });
  await loadRules();
}

async function deleteRule(id: number) {
  if (!confirm("このルールを削除しますか？")) return;
  await api.delete(`/categories/rules/${id}`);
  await loadRules();
}
</script>

<style scoped>
.page { padding: 1.5rem; max-width: 1200px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.page-header h2 { margin: 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th, td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #e8e8e8; text-align: left; }
th { background: #f5f5f0; font-weight: 600; }
.cond-cell pre { margin: 0; font-size: 0.75rem; white-space: pre-wrap; max-width: 220px; }
.badge { background: #e8eaf0; padding: 2px 8px; border-radius: 10px; font-size: 0.8rem; }
.active-dot { color: #27ae60; font-weight: 600; }
.inactive-dot { color: #aaa; }
.action-cell { white-space: nowrap; }
.btn-sm { padding: 0.3rem 0.7rem; font-size: 0.82rem; border: 1px solid #ccc; border-radius: 5px; cursor: pointer; margin-right: 4px; background: #fff; }
.btn-sm.danger { border-color: #e74c3c; color: #e74c3c; }
.btn-primary { background: #1a2340; color: #fff; border: none; border-radius: 8px; padding: 0.55rem 1.2rem; font-size: 0.92rem; cursor: pointer; font-weight: 600; }
.save-order-btn { margin-top: 1rem; }
.error-msg { color: #c0392b; font-size: 0.875rem; margin: 0.5rem 0; }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: #fff; padding: 1.5rem; border-radius: 12px; width: 480px; max-height: 90vh; overflow-y: auto; }
.modal h3 { margin: 0 0 1rem; }
.modal label { display: block; font-size: 0.83rem; font-weight: 600; color: #444; margin-top: 0.75rem; margin-bottom: 0.25rem; }
.modal input, .modal select, .modal textarea { width: 100%; box-sizing: border-box; padding: 0.5rem 0.7rem; border: 1px solid #ccc; border-radius: 6px; font-size: 0.9rem; }
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

  .action-cell {
    min-width: 190px;
  }

  .btn-sm,
  .btn-primary {
    width: 100%;
    margin-right: 0;
  }

  .modal {
    width: calc(100vw - 1.2rem);
    padding: 1rem;
    max-height: 86vh;
  }

  .modal-actions {
    flex-direction: column;
  }
}
</style>
