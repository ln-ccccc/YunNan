import axios from "axios";

import global from "@/global";

const authClient = axios.create({
  baseURL: global.BASEURL,
  withCredentials: true,
  // 会话探测挂在路由守卫上（每次导航必达）：无超时的请求在后端 worker 被
  // 上传/推理占满时无限 pending，守卫卡死表现为点击菜单无反应（2026-10-26 实测）
  timeout: 10000,
});

// 会话校验缓存：守卫周期性确认而非每次导航打请求；会话中途失效由 request.js
// 的 401 拦截器 redirectToLegacyLogin 兜底
const SESSION_NAV_CACHE_MS = 5 * 60 * 1000;
let sessionFreshAt = 0;

export function markSessionFresh() {
  sessionFreshAt = Date.now();
}

export function isSessionFresh() {
  return Date.now() - sessionFreshAt < SESSION_NAV_CACHE_MS;
}

export function invalidateSessionCache() {
  sessionFreshAt = 0;
}

export { SESSION_NAV_CACHE_MS };

export function legacyLogin(payload) {
  return authClient.post("/api/auth/login", payload).then((response) => {
    invalidateSessionCache();
    return response;
  });
}

export function legacySession() {
  return authClient.get("/api/auth/session");
}

export function legacyLogout() {
  return authClient.post("/api/auth/logout").finally(invalidateSessionCache);
}
