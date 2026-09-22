<template>
  <div v-if="initializing" class="loading-shell">正在检查登录状态...</div>
  <LoginPage
    v-else-if="currentView === 'login'"
    :submitting="authLoading"
    :error="authError"
    @login="handleLogin"
  />
  <div v-else class="app-shell" :class="{ 'with-sidebar': showSidebar }">
    <aside v-if="showSidebar" class="side-nav">
      <div class="brand">矿山监测<br />主控台</div>
      <nav class="nav-list">
        <button
          v-for="item in navItems"
          :key="item.key"
          :class="{ active: currentView === item.key || (item.key === 'projects' && currentView === 'map') }"
          @click="navigateToView(item)"
        >
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="!item.implemented" class="nav-badge">规划中</span>
        </button>
      </nav>
      <div class="nav-footer">
        <span class="username">{{ sessionState.username }}</span>
        <button class="logout-btn" @click="handleLogout">退出</button>
      </div>
    </aside>
    <main class="content-area" :class="{ scrollable: currentView !== 'map' }">
      <div v-show="currentView === 'projects' || currentView === 'map'" class="workspace-routed">
        <ProjectWorkspace
          v-show="currentView === 'projects'"
          :username="sessionState.username"
          @open-map="openMapView"
        />
        <MapDashboard
          v-show="currentView === 'map'"
          ref="mapDashboardRef"
          :key="selectedProjectId"
          :project-id="selectedProjectId"
          :username="sessionState.username"
          headerActionLabel="返回项目工作台"
          @back-to-projects="returnToProjectsView"
        />
      </div>
      <EditingView v-if="currentView === 'editing'" @go-projects="navigateToView(navItems[0])" />
      <ImageryView v-else-if="currentView === 'imagery'" @go-map="openMapView" />
      <SearchView v-else-if="currentView === 'search'" @open-map="openMapView" />
      <SettingsView v-else-if="currentView === 'settings'" :username="sessionState.username" />
      <ModulePlaceholder
        v-else-if="placeholderItem"
        :label="placeholderItem.label"
        :milestone="placeholderItem.milestone"
        @go-projects="navigateToView(navItems[0])"
      />
    </main>
  </div>
</template>

<script setup>
import axios from 'axios';
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';

import { resolveInitialView } from './auth/authGuards.js';
import { fetchSession, login as loginRequest, logout as logoutRequest } from './auth/sessionClient.js';
import LoginPage from './components/LoginPage.vue';
import MapDashboard from './components/MapDashboard.vue';
import EditingView from './components/EditingView.vue';
import ImageryView from './components/ImageryView.vue';
import SearchView from './components/SearchView.vue';
import SettingsView from './components/SettingsView.vue';
import ModulePlaceholder from './components/ModulePlaceholder.vue';
import ProjectWorkspace from './components/ProjectWorkspace.vue';
import { NAV_ITEMS, VIEW_HASH, buildMapHash, parseProjectIdFromHash, resolveViewFromHash } from './navigation/viewNavigation.js';

// 占位模块 → 交付里程碑标注（M1 骨架诚实占位，M3/M4 填充）
const PLACEHOLDER_MILESTONES = {
  interpretation: '项目工作台内发起',
  data: '项目工作台内操作',
};

const currentView = ref('login');
const mapDashboardRef = ref(null);
const pendingFocusMineFid = ref(null);
const selectedProjectId = ref(null);
const initializing = ref(true);
const authLoading = ref(false);
const authError = ref('');
const sessionState = ref({ authenticated: false, username: '' });

const navItems = NAV_ITEMS;
const showSidebar = computed(() => currentView.value !== 'login');
const placeholderItem = computed(() => {
  if (!PLACEHOLDER_MILESTONES[currentView.value]) return null;
  const item = NAV_ITEMS.find((entry) => entry.key === currentView.value);
  return item ? { ...item, milestone: PLACEHOLDER_MILESTONES[currentView.value] } : null;
});

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
    : (VIEW_HASH[view] || VIEW_HASH.projects);
  if (window.location.hash !== targetHash) {
    window.location.hash = targetHash;
  }
};

const navigateToView = (item) => {
  if (!sessionState.value.authenticated) {
    navigateToLogin();
    return;
  }
  selectedProjectId.value = null;
  currentView.value = item.key;
  syncHash(item.key);
};

const applyPendingMineFocus = async () => {
  if (pendingFocusMineFid.value === null || pendingFocusMineFid.value === undefined) return;
  await nextTick();
  mapDashboardRef.value?.focusByFid?.(pendingFocusMineFid.value);
  pendingFocusMineFid.value = null;
};

const openMapView = async (projectId, mineFid = null, options = {}) => {
  if (!sessionState.value.authenticated) {
    navigateToLogin();
    return;
  }
  if (!projectId) return;
  selectedProjectId.value = Number(projectId);
  pendingFocusMineFid.value = mineFid;
  currentView.value = 'map';
  syncHash('map');
  await nextTick();
  if (options?.openInference) mapDashboardRef.value?.openInferenceModal?.();
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
.app-shell {
  width: 100vw;
  height: 100vh;
  display: flex;
}

.with-sidebar .content-area {
  flex: 1;
  min-width: 0;
}

.side-nav {
  width: 200px;
  flex-shrink: 0;
  background: #1d3b36;
  color: #e8f2ef;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  gap: 16px;
}

.brand {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
  padding: 4px 8px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}

.nav-list { display: flex; flex-direction: column; gap: 4px; flex: 1; }

.nav-list button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: transparent;
  border: none;
  color: #cfe3de;
  padding: 10px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  text-align: left;
}

.nav-list button:hover { background: rgba(255, 255, 255, 0.08); }

.nav-list button.active {
  background: #2f6f61;
  color: #fff;
}

.nav-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.14);
  color: #b8d4cd;
}

.nav-list button.active .nav-badge { background: rgba(255, 255, 255, 0.22); }

.nav-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 8px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  font-size: 13px;
}

.username { color: #b8d4cd; }

.logout-btn {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.25);
  color: #cfe3de;
  border-radius: 5px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 12px;
}
.logout-btn:hover { background: rgba(255, 255, 255, 0.1); }

.content-area { position: relative; }

.content-area.scrollable { overflow-y: auto; }

.workspace-routed { width: 100%; height: 100%; }

.workspace-routed > div { width: 100%; height: 100%; }

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
