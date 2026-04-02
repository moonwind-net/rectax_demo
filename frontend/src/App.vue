<template>
  <div class="app-root">
    <div v-if="!isLogin" class="shell" :class="{ 'nav-open': navOpen }">
      <header class="mobile-topbar">
        <button class="menu-btn" type="button" @click="navOpen = !navOpen">☰ メニュー</button>
        <span class="mobile-brand">JP Receipt Console</span>
      </header>
      <div v-if="navOpen" class="mobile-backdrop" @click="navOpen = false"></div>
      <aside class="sidebar">
        <h1 class="brand">JP Receipt<br />Console</h1>
        <nav>
          <router-link to="/upload" @click="closeNav">📤 アップロード</router-link>
          <router-link to="/documents" @click="closeNav">📋 書類一覧</router-link>
          <router-link to="/review" @click="closeNav">🔍 レビュー</router-link>
          <router-link to="/export" @click="closeNav">📦 エクスポート</router-link>
          <hr class="nav-divider" />
          <router-link to="/rules" @click="closeNav">⚙️ 分類ルール</router-link>
          <router-link to="/company-tax-rules" @click="closeNav">🏢 会社税務ルール</router-link>
          <router-link to="/templates" @click="closeNav">📄 仕訳テンプレート</router-link>
          <router-link to="/correction-templates" @click="closeNav">🏷️ 修正テンプレート</router-link>
        </nav>
        <div class="company-switcher" v-if="companyOptions.length > 0">
          <label class="company-switcher-label">当前会社</label>
          <select v-model="selectedCompanyId" class="company-select" @change="onCompanyChange">
            <option v-for="company in companyOptions" :key="company.id" :value="String(company.id)">
              {{ company.name }}
            </option>
          </select>
        </div>
        <button class="logout-btn" @click="logout">ログアウト</button>
      </aside>
      <main class="content">
        <router-view />
      </main>
    </div>
    <router-view v-else />

    <div v-if="showVersionBadge" class="version-badge">v{{ appVersion }} | {{ buildTime }}</div>
    <button
      class="badge-toggle-zone"
      type="button"
      aria-label="toggle-version-badge"
      @click="onToggleZoneClick"
    ></button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, clearToken, getSelectedCompanyId, isLoggedIn, saveSelectedCompanyId } from "./services/api";

const route = useRoute();
const router = useRouter();
const navOpen = ref(false);
const appVersion = __APP_VERSION__;
const buildTime = __BUILD_TIME__;
const badgeToggleTapCount = ref(0);
const BADGE_TOGGLE_STORAGE_KEY = "rectax_show_version_badge";

const defaultShowBadge = import.meta.env.DEV;
const urlBadgeParam = new URLSearchParams(window.location.search).get("badge");
const urlBadgeOverride =
  urlBadgeParam === "1" ? true : urlBadgeParam === "0" ? false : null;
const persistedBadgeFlag = localStorage.getItem(BADGE_TOGGLE_STORAGE_KEY);
const showVersionBadge = ref(
  urlBadgeOverride ??
    (persistedBadgeFlag === null ? defaultShowBadge : persistedBadgeFlag === "1")
);
const companyOptions = ref<Array<{ id: number; name: string }>>([]);
const selectedCompanyId = ref("");

let badgeTapTimer: number | null = null;

const isLogin = computed(() => route.path === "/login");

function closeNav() {
  navOpen.value = false;
}

async function loadSessionCompanies() {
  if (!isLoggedIn() || route.path === "/login") return;
  try {
    const { data } = await api.get("/auth/session");
    companyOptions.value = data.companies ?? [];
    const stored = getSelectedCompanyId();
    const fallback = companyOptions.value[0] ? String(companyOptions.value[0].id) : "";
    const chosen = stored && companyOptions.value.some((company) => String(company.id) === stored) ? stored : fallback;
    if (chosen) {
      selectedCompanyId.value = chosen;
      saveSelectedCompanyId(chosen);
    }
  } catch {
    companyOptions.value = [];
    selectedCompanyId.value = "";
  }
}

function onCompanyChange() {
  if (selectedCompanyId.value) {
    saveSelectedCompanyId(selectedCompanyId.value);
    if (route.path !== "/login") {
      window.location.reload();
    }
  }
}

function onToggleZoneClick() {
  badgeToggleTapCount.value += 1;

  if (badgeTapTimer !== null) {
    window.clearTimeout(badgeTapTimer);
  }

  badgeTapTimer = window.setTimeout(() => {
    badgeToggleTapCount.value = 0;
  }, 2500);

  if (badgeToggleTapCount.value >= 5) {
    const next = !showVersionBadge.value;
    showVersionBadge.value = next;
    localStorage.setItem(BADGE_TOGGLE_STORAGE_KEY, next ? "1" : "0");
    badgeToggleTapCount.value = 0;
    if (badgeTapTimer !== null) {
      window.clearTimeout(badgeTapTimer);
      badgeTapTimer = null;
    }
  }
}

function logout() {
  closeNav();
  clearToken();
  router.push("/login");
}

onMounted(loadSessionCompanies);

watch(
  () => route.path,
  () => {
    void loadSessionCompanies();
  }
);
</script>

<style scoped>
.app-root {
  min-height: 100vh;
}

.shell { display: flex; min-height: 100vh; }
.mobile-topbar { display: none; }
.mobile-backdrop { display: none; }
.sidebar {
  width: 200px;
  flex-shrink: 0;
  background: #1a2340;
  color: #fff;
  padding: 1.5rem 1rem;
  display: flex;
  flex-direction: column;
}
.brand { font-size: 1rem; font-weight: 700; line-height: 1.4; margin-bottom: 1.5rem; }
nav { display: flex; flex-direction: column; gap: 0.25rem; flex: 1; }
nav a {
  color: #c8d0e0;
  text-decoration: none;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.9rem;
  transition: background 0.15s;
}
nav a:hover, nav a.router-link-active { background: rgba(255,255,255,0.12); color: #fff; }
.nav-divider { border: none; border-top: 1px solid rgba(255,255,255,0.12); margin: 0.5rem 0; }
.logout-btn {
  margin-top: auto;
  background: transparent;
  border: 1px solid rgba(255,255,255,0.25);
  color: #c8d0e0;
  border-radius: 6px;
  padding: 0.45rem 0.75rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.logout-btn:hover { background: rgba(255,255,255,0.08); }
.company-switcher {
  margin-top: 0.9rem;
  margin-bottom: 0.9rem;
}
.company-switcher-label {
  display: block;
  font-size: 0.76rem;
  color: #c8d0e0;
  margin-bottom: 0.3rem;
}
.company-select {
  width: 100%;
  background: rgba(255,255,255,0.08);
  color: #fff;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 6px;
  padding: 0.45rem 0.55rem;
  font-size: 0.85rem;
}
.company-select option { color: #111827; }
.content { flex: 1; background: #f5f5f0; overflow-y: auto; }

.version-badge {
  position: fixed;
  right: 10px;
  bottom: 10px;
  z-index: 120;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(20, 33, 61, 0.88);
  color: #f3f6ff;
  font-size: 11px;
  line-height: 1;
  letter-spacing: 0.2px;
  pointer-events: none;
}

.badge-toggle-zone {
  position: fixed;
  right: 6px;
  bottom: 6px;
  width: 28px;
  height: 28px;
  border: 0;
  background: transparent;
  opacity: 0;
  z-index: 121;
  cursor: default;
  -webkit-tap-highlight-color: transparent;
}

@media (max-width: 900px) {
  .shell {
    display: block;
    min-height: 100dvh;
  }

  .mobile-topbar {
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    gap: 0.7rem;
    height: 56px;
    padding: 0 0.8rem;
    background: #1a2340;
    color: #fff;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  }

  .menu-btn {
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 6px;
    background: transparent;
    color: #fff;
    padding: 0.35rem 0.65rem;
    font-size: 0.85rem;
    width: auto;
    cursor: pointer;
  }

  .mobile-brand {
    font-size: 0.9rem;
    font-weight: 700;
    letter-spacing: 0.2px;
  }

  .mobile-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    z-index: 19;
  }

  .sidebar {
    position: fixed;
    inset: 56px auto 0 0;
    width: min(82vw, 290px);
    transform: translateX(-105%);
    transition: transform 0.2s ease;
    z-index: 20;
    overflow-y: auto;
  }

  .shell.nav-open .sidebar {
    transform: translateX(0);
  }

  .brand {
    margin-bottom: 0.9rem;
  }

  .content {
    min-height: calc(100dvh - 56px);
    padding-bottom: 1rem;
  }

  .version-badge {
    right: 8px;
    bottom: 8px;
    font-size: 10px;
    padding: 4px 7px;
  }

  .badge-toggle-zone {
    right: 4px;
    bottom: 4px;
    width: 30px;
    height: 30px;
  }
}
</style>

