<template>
  <div class="login-wrapper">
    <div class="login-card">
      <h1 class="login-title">領収書処理システム</h1>
      <p class="login-subtitle">ログイン</p>

      <form @submit.prevent="handleLogin">
        <div class="field">
          <label>メールアドレス</label>
          <input v-model="form.email" type="email" autocomplete="username" required />
        </div>
        <div class="field">
          <label>パスワード</label>
          <input v-model="form.password" type="password" autocomplete="current-password" required />
        </div>
        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>
        <button type="submit" :disabled="loading" class="btn-primary full-width">
          {{ loading ? "ログイン中..." : "ログイン" }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, saveTokens } from "../services/api";

const router = useRouter();
const form = ref({ email: "", password: "" });
const errorMsg = ref("");
const loading = ref(false);

async function handleLogin() {
  errorMsg.value = "";
  loading.value = true;
  try {
    const res = await api.post("/auth/login", form.value);
    saveTokens(res.data.access_token, res.data.refresh_token);
    router.push("/upload");
  } catch (err: any) {
    errorMsg.value = err.response?.data?.detail ?? "ログインに失敗しました";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-wrapper {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f5f0;
}
.login-card {
  background: #fff;
  border-radius: 12px;
  padding: 2.5rem 2rem;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
}
.login-title {
  font-size: 1.4rem;
  font-weight: 700;
  color: #1a2340;
  text-align: center;
  margin-bottom: 0.25rem;
}
.login-subtitle {
  text-align: center;
  color: #666;
  margin-bottom: 1.5rem;
  font-size: 0.95rem;
}
.field {
  margin-bottom: 1rem;
}
.field label {
  display: block;
  font-size: 0.85rem;
  color: #444;
  margin-bottom: 0.3rem;
  font-weight: 500;
}
.field input {
  width: 100%;
  padding: 0.6rem 0.8rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 0.95rem;
  box-sizing: border-box;
}
.error-msg {
  color: #c0392b;
  font-size: 0.875rem;
  margin-bottom: 0.75rem;
}
.btn-primary {
  background: #1a2340;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.65rem 1.5rem;
  font-size: 0.95rem;
  cursor: pointer;
  font-weight: 600;
}
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.full-width { width: 100%; }

@media (max-width: 900px) {
  .login-wrapper {
    padding: 1rem;
    align-items: flex-start;
    padding-top: max(1rem, env(safe-area-inset-top));
  }

  .login-card {
    margin-top: 2.5rem;
    padding: 1.25rem 1rem;
    border-radius: 10px;
  }

  .login-title {
    font-size: 1.2rem;
  }
}
</style>
