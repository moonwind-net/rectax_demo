import { createRouter, createWebHistory } from "vue-router";
import { isLoggedIn } from "../services/api";

import LoginPage from "../pages/LoginPage.vue";
import UploadCenter from "../pages/UploadCenter.vue";
import DocumentList from "../pages/DocumentList.vue";
import ReviewQueue from "../pages/ReviewQueue.vue";
import DocumentView from "../pages/DocumentView.vue";
import ExportCenter from "../pages/ExportCenter.vue";
import RulesEditor from "../pages/RulesEditor.vue";
import TemplatesEditor from "../pages/TemplatesEditor.vue";
import CorrectionTemplatesEditor from "../pages/CorrectionTemplatesEditor.vue";
import CompanyTaxRulesEditor from "../pages/CompanyTaxRulesEditor.vue";

const routes = [
  { path: "/login", component: LoginPage, meta: { public: true } },
  { path: "/", redirect: "/upload" },
  { path: "/upload", component: UploadCenter },
  { path: "/documents", component: DocumentList },
  { path: "/documents/:id", component: DocumentView },
  { path: "/review", component: ReviewQueue },
  { path: "/export", component: ExportCenter },
  { path: "/rules", component: RulesEditor },
  { path: "/templates", component: TemplatesEditor },
  { path: "/correction-templates", component: CorrectionTemplatesEditor },
  { path: "/company-tax-rules", component: CompanyTaxRulesEditor },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  if (!to.meta.public && !isLoggedIn()) {
    return { path: "/login" };
  }
});

export default router;
