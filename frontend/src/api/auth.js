import axios from "axios";

import global from "@/global";

const authClient = axios.create({
  baseURL: global.BASEURL,
  withCredentials: true,
});

export function legacyLogin(payload) {
  return authClient.post("/api/auth/login", payload);
}

export function legacySession() {
  return authClient.get("/api/auth/session");
}

export function legacyLogout() {
  return authClient.post("/api/auth/logout");
}
