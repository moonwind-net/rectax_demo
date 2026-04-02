<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../services/api";

const route = useRoute();
const id = Number(route.params.id);

const documentMeta = ref<any>(null);
const previewUrl = ref("");
const previewType = ref("");
const loading = ref(true);
const error = ref("");

async function ensurePreview(documentId: number) {
  try {
    const res = await api.get(`/documents/${documentId}/preview`, { responseType: "blob" });
    previewUrl.value = URL.createObjectURL(res.data);
    previewType.value = res.headers["content-type"] ?? "";
  } catch (e: any) {
    previewUrl.value = "";
    previewType.value = "";
  }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    // Try fetching single document first, fallback to list
    try {
      const { data } = await api.get(`/documents/${id}`);
      documentMeta.value = data;
    } catch {
      const { data } = await api.get("/documents");
      documentMeta.value = data.find((d: any) => d.id === id) ?? null;
    }
    await ensurePreview(id);
  } catch (err: any) {
    error.value = err?.message ?? String(err);
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  load();
});
</script>

<template>
  <section class="card">
    <div class="toolbar">
      <h2>書類プレビュー</h2>
      <button class="secondary" @click="load">再読み込み</button>
    </div>

    <div v-if="loading" class="action-message">読み込み中…</div>
    <div v-else-if="error" class="action-message">読み込みエラー: {{ error }}</div>
    <div v-else>
      <p class="doc-meta">{{ documentMeta?.original_filename ?? `ID: ${id}` }}</p>

      <div v-if="previewUrl && previewType.startsWith('image/')" class="preview-stage">
        <img :src="previewUrl" alt="document preview" class="preview-image" />
      </div>
      <div v-else-if="previewUrl && previewType.includes('pdf')">
        <iframe :src="previewUrl" class="preview-pdf" title="pdf preview"></iframe>
      </div>
      <p v-else class="preview-empty">プレビューがありません（承認済みや古いファイルはプレビュー未作成の可能性があります）。</p>

      <section class="meta-list">
        <p><strong>ステータス:</strong> {{ documentMeta?.document_status ?? '-' }}</p>
        <p><strong>店舗名:</strong> {{ documentMeta?.merchant_name ?? '-' }}</p>
        <p><strong>合計金額:</strong> {{ documentMeta?.total_amount ?? '-' }}</p>
        <p><strong>最終更新:</strong> {{ documentMeta?.updated_at ?? '-' }}</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.preview-stage { text-align: center; margin: 12px 0; }
.preview-image { max-width: 100%; height: auto; border: 1px solid #e3edf7; border-radius: 8px; }
.preview-pdf { width: 100%; height: 720px; border: none; }
.doc-meta { font-weight: 700; margin: 8px 0; }
.meta-list p { margin: 6px 0; }
</style>
