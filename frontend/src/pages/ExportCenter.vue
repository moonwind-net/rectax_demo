<script setup lang="ts">
import { ref } from "vue";
import { api } from "../services/api";

const templateId = ref<number>(1);
const onlyApproved = ref(true);
const result = ref("");

async function runExport() {
  const { data } = await api.post("/exports", {
    template_id: templateId.value,
    only_approved: onlyApproved.value
  });
  result.value = `エクスポート完了。ファイルID=${data.export_file_id}、パス=${data.file_path}`;
}
</script>

<template>
  <section class="card">
    <h2>仕訳エクスポートセンター</h2>
    <div class="toolbar">
      <div class="field field-wide">
        <label>出力テンプレートID</label>
        <input type="number" v-model.number="templateId" />
      </div>
      <div class="field">
        <label>
          <input type="checkbox" v-model="onlyApproved" />
          承認済み仕訳のみを出力
        </label>
      </div>
      <button @click="runExport">CSV を生成</button>
    </div>
    <p>{{ result }}</p>
  </section>
</template>

<style scoped>
.field {
  min-width: 0;
}

.field-wide {
  min-width: 220px;
}

@media (max-width: 900px) {
  .field-wide {
    min-width: 0;
  }
}
</style>