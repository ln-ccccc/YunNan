<template>
  <div v-if="initializing" class="loading-shell">正在检查登录状态...</div>
  <LoginPage
    v-else-if="currentView === 'login'"
    :submitting="authLoading"
    :error="authError"
    @login="handleLogin"
  />
  <div v-else class="app-shell">
    <div v-show="currentView === 'projects'" class="workspace-shell">
      <ProjectWorkspace
        :username="sessionState.username"
        @open-map="openMapView"
        @logout="handleLogout"
      />
    </div>
    <div v-show="currentView === 'map'" class="map-shell">
      <MapDashboard
        ref="mapDashboardRef"
        :key="selectedProjectId"
        :project-id="selectedProjectId"
        :username="sessionState.username"
        headerActionLabel="返回项目工作台"
        @back-to-projects="returnToProjectsView"
        @logout="handleLogout"
      />
    </div>
  </div>
</template>

<script setup>
import axios from 'axios';
import { nextTick, onMounted, onUnmounted, ref } from 'vue';

import { resolveInitialView } from './auth/authGuards.js';
import { fetchSession, login as loginRequest, logout as logoutRequest } from './auth/sessionClient.js';
import LoginPage from './components/LoginPage.vue';
import MapDashboard from './components/MapDashboard.vue';
import ProjectWorkspace from './components/ProjectWorkspace.vue';
import { VIEW_HASH, buildMapHash, parseProjectIdFromHash, resolveViewFromHash } from './navigation/viewNavigation.js';

const currentView = ref('login');
const mapDashboardRef = ref(null);
const pendingFocusMineFid = ref(null);
const selectedProjectId = ref(null);
const initializing = ref(true);
const authLoading = ref(false);
const authError = ref('');
const sessionState = ref({ authenticated: false, username: '' });

let responseInterceptorId = null;

const navigateToLogin = (message = '') => {
  sessionState.value = { authenticated: false, username: '' };
  currentView.value = 'login';
  authError.value = message;
  if (window.location.hash !== VIEW_HASH.projects) {
    window.history.replaceState(null, '', VIEW_HASH.projects);
  }
};

const syncHash = (view) => {
  const targetHash = view === 'map' && selectedProjectId.value
    ? buildMapHash(selectedProjectId.value)
    : VIEW_HASH.projects;
  if (window.location.hash !== targetHash) {
    window.location.hash = targetHash;
  }
};

const applyPendingMineFocus = async () => {
  if (pendingFocusMineFid.value === null || pendingFocusMineFid.value === undefined) return;
  await nextTick();
  mapDashboardRef.value?.focusByFid?.(pendingFocusMineFid.value);
  pendingFocusMineFid.value = null;
};

const openMapView = async (projectId, mineFid = null) => {
  if (!sessionState.value.authenticated) {
    navigateToLogin();
    return;
  }
  if (!projectId) return;
  selectedProjectId.value = Number(projectId);
  pendingFocusMineFid.value = mineFid;
  currentView.value = 'map';
  syncHash('map');
  await applyPendingMineFocus();
};

const returnToProjectsView = () => {
  selectedProjectId.value = null;
  currentView.value = 'projects';
  syncHash('projects');
};

const handleHashChange = async () => {
  selectedProjectId.value = parseProjectIdFromHash(window.location.hash);
  currentView.value = sessionState.value.authenticated
    ? resolveViewFromHash(window.location.hash)
    : 'login';
  if (currentView.value === 'map') {
    await applyPendingMineFocus();
  }
};

const handleLogin = async ({ username, password }) => {
  authLoading.value = true;
  authError.value = '';
  try {
    sessionState.value = await loginRequest(username, password);
    currentView.value = resolveInitialView({
      authenticated: sessionState.value.authenticated,
      hash: window.location.hash || VIEW_HASH.projects,
    });
    if (currentView.value === 'login') {
      currentView.value = 'projects';
    }
    syncHash(currentView.value);
    await applyPendingMineFocus();
  } catch (error) {
    authError.value = error?.response?.data?.msg || error?.message || '登录失败';
  } finally {
    authLoading.value = false;
  }
};

const handleLogout = async () => {
  try {
    await logoutRequest();
  } catch (_) {
    // Ignore logout transport errors and clear the local session shell.
  }
  navigateToLogin('');
};

onMounted(async () => {
  if (!window.location.hash) {
    window.history.replaceState(null, '', VIEW_HASH.projects);
  }
  responseInterceptorId = axios.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error?.response?.status === 401) {
        navigateToLogin('登录已失效，请重新登录');
      }
      return Promise.reject(error);
    },
  );
  try {
    sessionState.value = await fetchSession();
    selectedProjectId.value = parseProjectIdFromHash(window.location.hash);
    currentView.value = resolveInitialView({
      authenticated: sessionState.value.authenticated,
      hash: window.location.hash,
    });
  } catch (_) {
    navigateToLogin('会话检查失败，请重新登录');
  } finally {
    initializing.value = false;
  }
  window.addEventListener('hashchange', handleHashChange);
});

onUnmounted(() => {
  window.removeEventListener('hashchange', handleHashChange);
  if (responseInterceptorId !== null) {
    axios.interceptors.response.eject(responseInterceptorId);
  }
});
</script>

<style scoped>
.app-shell,
.workspace-shell,
.map-shell {
  width: 100vw;
  height: 100vh;
}

.loading-shell {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #eef5f3 0%, #dce8e3 100%);
  color: #264b45;
  font-size: 18px;
}
</style>
