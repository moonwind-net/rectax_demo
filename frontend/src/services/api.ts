import axios from "axios";

export const TOKEN_KEY = "jwt_access_token";
export const REFRESH_TOKEN_KEY = "jwt_refresh_token";
export const COMPANY_ID_KEY = "rectax_current_company_id";

export const api = axios.create({
  // Use same-origin API path so browser works with Docker/Nginx reverse proxy.
  baseURL: "/api/v1",
  timeout: 30000,
});

// Attach JWT from localStorage on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  const companyId = localStorage.getItem(COMPANY_ID_KEY);
  if (companyId) {
    config.headers["X-Client-Company-Id"] = companyId;
  }
  return config;
});

// Redirect to login on 401
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config as any;
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);

    if (error.response?.status === 401 && !originalRequest?._retry && refreshToken) {
      originalRequest._retry = true;
      try {
        const refreshClient = axios.create({ baseURL: "/api/v1", timeout: 30000 });
        const res = await refreshClient.post("/auth/refresh", { refresh_token: refreshToken });
        saveTokens(res.data.access_token, res.data.refresh_token);
        originalRequest.headers = originalRequest.headers ?? {};
        originalRequest.headers.Authorization = `Bearer ${res.data.access_token}`;
        return api(originalRequest);
      } catch {
        clearToken();
        window.location.href = "/login";
        return Promise.reject(error);
      }
    }

    if (error.response?.status === 401) {
      clearToken();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export function saveToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function saveTokens(accessToken: string, refreshToken: string) {
  localStorage.setItem(TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function saveSelectedCompanyId(companyId: number | string) {
  localStorage.setItem(COMPANY_ID_KEY, String(companyId));
}

export function getSelectedCompanyId(): string | null {
  return localStorage.getItem(COMPANY_ID_KEY);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(COMPANY_ID_KEY);
}

export function isLoggedIn(): boolean {
  return !!localStorage.getItem(TOKEN_KEY);
}
