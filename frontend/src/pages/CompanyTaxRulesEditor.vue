<template>
  <div class="page">
    <div class="page-header">
      <h2>会社別 税額取整ルール</h2>
      <button class="btn-primary" @click="load">再読み込み</button>
    </div>

    <p class="page-note">
      会社ごとに「円単位の取整方式」と「税額の取整粒度」を管理します。未設定時はサーバー既定値にフォールバックします。
    </p>

    <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>
    <p v-if="successMsg" class="success-msg">{{ successMsg }}</p>

    <section class="preview-card" v-if="previewCompany">
      <div class="preview-head">
        <div>
          <h3>示例预览计算</h3>
          <p>预览公司: {{ previewCompany.name }} / {{ roundingModeLabel(previewCompany.jpy_rounding_mode) }} / {{ levelLabel(previewCompany.tax_rounding_level) }}</p>
        </div>
        <select v-model="previewCompanyId">
          <option v-for="company in companies" :key="company.id" :value="String(company.id)">{{ company.name }}</option>
        </select>
      </div>

      <div class="preview-grid">
        <div class="preview-field">
          <label>含税总额（円）</label>
          <input v-model.number="previewTotalAmount" type="number" min="0" step="1" />
        </div>
      </div>

      <div class="preview-lines">
        <div class="preview-line preview-line--header">
          <span>税率</span>
          <span>课税対象額</span>
          <span>raw税額</span>
        </div>
        <div v-for="(line, index) in previewLines" :key="index" class="preview-line">
          <select v-model="line.tax_rate">
            <option value="8">8%</option>
            <option value="10">10%</option>
          </select>
          <input v-model.number="line.taxable_amount" type="number" min="0" step="1" />
          <span class="mono">{{ ((line.taxable_amount || 0) * Number(line.tax_rate) / 100).toFixed(2) }}</span>
        </div>
      </div>

      <div class="result-table">
        <div class="result-row result-row--header">
          <span>模式</span>
          <span>规则</span>
          <span>税额明细</span>
          <span>税额合计</span>
        </div>
        <div v-for="result in previewResults" :key="result.key" class="result-row" :class="{ 'result-row--active': previewCompany.tax_rounding_level === result.level }">
          <span>{{ result.key }}</span>
          <span>{{ roundingModeLabel(previewCompany.jpy_rounding_mode) }} / {{ levelLabel(result.level) }}</span>
          <span class="mono">{{ result.lineTaxes.join(' / ') }}</span>
          <span class="mono">{{ result.totalTax.toLocaleString() }}円</span>
        </div>
      </div>
    </section>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>会社コード</th>
            <th>会社名</th>
            <th>登録番号</th>
            <th>円取整</th>
            <th>税額取整粒度</th>
            <th>状態</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="company in companies" :key="company.id">
            <td>{{ company.id }}</td>
            <td class="mono">{{ company.code }}</td>
            <td>{{ company.name }}</td>
            <td class="mono">{{ company.registration_number || '—' }}</td>
            <td>
              <select v-model="company.jpy_rounding_mode">
                <option value="floor">切り捨て</option>
                <option value="round">四捨五入</option>
                <option value="ceil">切り上げ</option>
              </select>
            </td>
            <td>
              <select v-model="company.tax_rounding_level">
                <option value="document">単票全体</option>
                <option value="tax_rate">税率分组</option>
                <option value="line">明細行ごと</option>
              </select>
            </td>
            <td>
              <span :class="company.is_active ? 'active-dot' : 'inactive-dot'">
                {{ company.is_active ? '有効' : '無効' }}
              </span>
            </td>
            <td>
              <button class="btn-sm" :disabled="savingId === company.id" @click="save(company)">
                {{ savingId === company.id ? '保存中...' : '保存' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../services/api";

interface CompanyTaxRule {
  id: number;
  accounting_firm_id: number;
  code: string;
  name: string;
  registration_number?: string | null;
  is_active: boolean;
  jpy_rounding_mode: "floor" | "round" | "ceil";
  tax_rounding_level: "document" | "tax_rate" | "line";
}

const companies = ref<CompanyTaxRule[]>([]);
const errorMsg = ref("");
const successMsg = ref("");
const savingId = ref<number | null>(null);
const previewCompanyId = ref("");
const previewTotalAmount = ref(1080);
const previewLines = ref([
  { tax_rate: "8", taxable_amount: 540 },
  { tax_rate: "10", taxable_amount: 540 },
]);

const previewCompany = computed(() => companies.value.find((company) => String(company.id) === previewCompanyId.value) ?? companies.value[0] ?? null);

function roundYen(value: number, mode: CompanyTaxRule["jpy_rounding_mode"]) {
  if (mode === "floor") return Math.floor(value);
  if (mode === "ceil") return Math.ceil(value);
  return Math.round(value);
}

function allocateByCumulative(rawValues: number[], mode: CompanyTaxRule["jpy_rounding_mode"]) {
  const assigned: number[] = [];
  let cumulativeRaw = 0;
  let cumulativeAssigned = 0;
  for (const rawValue of rawValues) {
    cumulativeRaw += rawValue;
    const nextCumulative = roundYen(cumulativeRaw, mode);
    const current = nextCumulative - cumulativeAssigned;
    assigned.push(current);
    cumulativeAssigned += current;
  }
  return assigned;
}

function levelLabel(level: CompanyTaxRule["tax_rounding_level"]) {
  if (level === "document") return "単票全体";
  if (level === "line") return "明細行ごと";
  return "税率分组";
}

function roundingModeLabel(mode: CompanyTaxRule["jpy_rounding_mode"]) {
  if (mode === "floor") return "切り捨て";
  if (mode === "ceil") return "切り上げ";
  return "四捨五入";
}

const previewResults = computed(() => {
  const company = previewCompany.value;
  if (!company) return [];
  const rawTaxes = previewLines.value.map((line) => (line.taxable_amount || 0) * Number(line.tax_rate) / 100);
  const levels: CompanyTaxRule["tax_rounding_level"][] = ["document", "tax_rate", "line"];
  return levels.map((level) => {
    let lineTaxes: number[] = [];
    if (level === "line") {
      lineTaxes = rawTaxes.map((value) => roundYen(value, company.jpy_rounding_mode));
    } else if (level === "document") {
      lineTaxes = allocateByCumulative(rawTaxes, company.jpy_rounding_mode);
    } else {
      lineTaxes = new Array(rawTaxes.length).fill(0);
      const grouped = new Map<string, number[]>();
      previewLines.value.forEach((line, index) => {
        const key = line.tax_rate;
        const indexes = grouped.get(key) ?? [];
        indexes.push(index);
        grouped.set(key, indexes);
      });
      grouped.forEach((indexes) => {
        const allocated = allocateByCumulative(indexes.map((index) => rawTaxes[index]), company.jpy_rounding_mode);
        indexes.forEach((index, position) => {
          lineTaxes[index] = allocated[position];
        });
      });
    }
    return {
      key: level === company.tax_rounding_level ? "当前配置" : level,
      level,
      lineTaxes,
      totalTax: lineTaxes.reduce((sum, value) => sum + value, 0),
    };
  });
});

async function load() {
  errorMsg.value = "";
  try {
    const { data } = await api.get("/company-settings/companies");
    companies.value = data;
    if (!previewCompanyId.value && companies.value.length > 0) {
      previewCompanyId.value = String(companies.value[0].id);
    }
  } catch {
    errorMsg.value = "会社ルールの読み込みに失敗しました";
  }
}

async function save(company: CompanyTaxRule) {
  savingId.value = company.id;
  errorMsg.value = "";
  successMsg.value = "";
  try {
    await api.put(`/company-settings/companies/${company.id}/tax-rules`, {
      jpy_rounding_mode: company.jpy_rounding_mode,
      tax_rounding_level: company.tax_rounding_level,
    });
    successMsg.value = `会社「${company.name}」の税額ルールを更新しました`;
  } catch (error: any) {
    errorMsg.value = error?.response?.data?.detail ?? "保存に失敗しました";
  } finally {
    savingId.value = null;
  }
}

onMounted(load);
</script>

<style scoped>
.page { padding: 1.5rem; max-width: 1200px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.page-header h2 { margin: 0; }
.page-note { margin: 0 0 1rem; color: #60758f; font-size: 0.92rem; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { padding: 0.65rem 0.75rem; border-bottom: 1px solid #e8edf5; text-align: left; }
th { background: #f5f8fc; font-weight: 700; color: #304763; }
select { min-width: 140px; padding: 0.38rem 0.5rem; border: 1px solid #cfd9e8; border-radius: 6px; background: #fff; }
.mono { font-family: Consolas, monospace; font-size: 0.82rem; }
.active-dot { color: #16a34a; font-weight: 700; }
.inactive-dot { color: #9ca3af; font-weight: 700; }
.btn-sm { padding: 0.38rem 0.8rem; font-size: 0.82rem; border: 1px solid #cfd9e8; border-radius: 6px; cursor: pointer; background: #fff; }
.btn-primary { background: #1a2340; color: #fff; border: none; border-radius: 8px; padding: 0.55rem 1.2rem; font-size: 0.92rem; cursor: pointer; font-weight: 600; }
.error-msg { color: #b91c1c; margin: 0 0 0.75rem; }
.success-msg { color: #15803d; margin: 0 0 0.75rem; }
.preview-card {
  border: 1px solid #d9e3f1;
  border-radius: 12px;
  background: #f8fbff;
  padding: 1rem;
  margin-bottom: 1rem;
}
.preview-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: end;
  margin-bottom: 0.8rem;
}
.preview-head h3 { margin: 0 0 0.2rem; }
.preview-head p { margin: 0; color: #60758f; font-size: 0.86rem; }
.preview-grid { display: grid; grid-template-columns: 220px; gap: 0.75rem; margin-bottom: 0.8rem; }
.preview-field label { display: block; margin-bottom: 0.25rem; font-size: 0.82rem; color: #304763; }
.preview-field input { width: 100%; padding: 0.45rem 0.55rem; border: 1px solid #cfd9e8; border-radius: 6px; }
.preview-lines { display: flex; flex-direction: column; gap: 0.35rem; margin-bottom: 0.8rem; }
.preview-line { display: grid; grid-template-columns: 100px 160px 1fr; gap: 0.6rem; align-items: center; }
.preview-line--header { font-size: 0.8rem; font-weight: 700; color: #60758f; }
.preview-line input { padding: 0.4rem 0.5rem; border: 1px solid #cfd9e8; border-radius: 6px; }
.result-table { border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: #fff; }
.result-row { display: grid; grid-template-columns: 110px 180px 1fr 120px; gap: 0.75rem; padding: 0.6rem 0.75rem; border-top: 1px solid #eef2f7; align-items: center; }
.result-row--header { background: #f5f8fc; font-weight: 700; border-top: 0; }
.result-row--active { background: #eefbf3; }
@media (max-width: 900px) {
  .preview-head,
  .preview-line,
  .result-row { grid-template-columns: 1fr; display: grid; }
  .preview-grid { grid-template-columns: 1fr; }
}
</style>