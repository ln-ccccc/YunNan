<template>
  <div v-if="initializing" class="loading-shell">正在检查登录状态...</div>
  <LoginPage
    v-else-if="currentView === 'login'"
    :submitting="authLoading"
    :error="authError"
    @login="handleLogin"
  />
  <div v-else class="app-shell" :class="{ 'with-sidebar': showSidebar }">
    <aside v-if="showSidebar" class="side-nav" :class="{ collapsed: navCollapsed }">
      <div class="brand" title="矿山监测主控台">
        <svg class="brand-mark" viewBox="0 0 40 40" aria-hidden="true"><path d="M5 29 16 10l8 14 5-8 7 13M10 29h21M13 16l3 4 3-4" /></svg>
        <div v-if="!navCollapsed" class="brand-copy">矿山监测<span>生态修复 · 项目工作台</span></div>
      </div>
      <nav class="nav-list" aria-label="主导航">
        <button type="button"
          v-for="item in navItems"
          :key="item.key"
          :class="{ active: currentView === item.key || (item.key === 'projects' && currentView === 'map') }"
          :title="navCollapsed ? item.label : null"
          :aria-label="item.label"
          :aria-current="currentView === item.key || (item.key === 'projects' && currentView === 'map') ? 'page' : undefined"
          @click="navigateToView(item)"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="navIconPaths[item.key]" /></svg>
          <span v-if="!navCollapsed" class="nav-label">{{ item.label }}</span>
          <span v-if="!navCollapsed && !item.implemented" class="nav-badge">规划中</span>
          <span v-else-if="navCollapsed && !item.implemented" class="nav-badge-dot" />
        </button>
      </nav>
      <div v-if="!navCollapsed" class="nav-footer">
        <span class="username">{{ sessionState.username }}</span>
        <button type="button" class="logout-btn" @click="handleLogout">退出</button>
      </div>
      <button type="button"
        v-else
        class="logout-btn logout-compact"
        title="退出登录"
        aria-label="退出登录"
        @click="handleLogout"
      ><svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v9 M6.34 5.66a8 8 0 1 0 11.32 0" /></svg></button>
    </aside>
    <main class="content-area" :class="{ scrollable: currentView !== 'map' }">
      <div v-show="currentView === 'projects' || currentView === 'map'" class="workspace-routed">
        <ProjectWorkspace
          v-show="currentView === 'projects'"
          :username="sessionState.username"
          @open-map="openMapView"
          @logout="handleLogout"
        />
        <MapDashboard
          v-if="selectedProjectId"
          v-show="currentView === 'map'"
          ref="mapDashboardRef"
          :key="selectedProjectId"
          :project-id="selectedProjectId"
          :username="sessionState.username"
          headerActionLabel="返回项目工作台"
          @back-to-projects="returnToProjectsView"
          @logout="handleLogout"
        />
      </div>
      <EditingView v-if="currentView === 'editing'" @go-projects="navigateToView(navItems[0])" />
      <ImageryView v-else-if="currentView === 'imagery'" @go-map="openMapView" />
      <SearchView v-else-if="currentView === 'search'" @open-map="openMapView" />
      <SmartInterpretationView v-else-if="currentView === 'interpretation'" @go-map="openMapView" />
      <DataManagerView v-else-if="currentView === 'data'" />
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
import SmartInterpretationView from './components/SmartInterpretationView.vue';
import DataManagerView from './components/DataManagerView.vue';
import ModulePlaceholder from './components/ModulePlaceholder.vue';
import ProjectWorkspace from './components/ProjectWorkspace.vue';
import { NAV_ITEMS, VIEW_HASH, buildMapHash, parseProjectIdFromHash, resolveViewFromHash } from './navigation/viewNavigation.js';

// 占位机制备用：M5.4 起七大模块全部转正，此表清空；后续新增模块可复用
const PLACEHOLDER_MILESTONES = {};

const currentView = ref('login');
const mapDashboardRef = ref(null);
const pendingFocusMineFid = ref(null);
const selectedProjectId = ref(null);
const initializing = ref(true);
const authLoading = ref(false);
const authError = ref('');
const sessionState = ref({ authenticated: false, username: '' });

const navItems = NAV_ITEMS;
const navIconPaths = {
  projects: 'M3 7h7l2-3h9v16H3z M3 9h18',
  imagery: 'M4 4h16v16H4z M4 16l5-5 4 4 3-3 4 4 M8 8h.01',
  interpretation: 'M12 3v3 M12 18v3 M3 12h3 M18 12h3 M8 8h8v8H8z',
  editing: 'M14 5l5 5 M4 20l5-1L20 8l-4-4L5 15z',
  data: 'M4 6c0-4 16-4 16 0s-16 4-16 0 M4 6v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0',
  search: 'M15 15l6 6 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  settings: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
};

// 侧栏自适应：≤1100px 折叠为图标条（恢复旧版 Home.vue 的响应式行为，AppShell 化时曾丢失）
const navCollapsed = ref(false);
const syncNavCollapse = () => {
  navCollapsed.value = document.documentElement.clientWidth <= 1100;
};
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
  syncNavCollapse();
  window.addEventListener('resize', syncNavCollapse);
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
  window.removeEventListener('resize', syncNavCollapse);
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
  transition: width 0.2s ease;
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

/* 折叠态（≤1100px 自动）：图标条 */
.side-nav.collapsed {
  width: 56px;
  padding: 16px 6px;
}
.side-nav.collapsed .brand {
  font-size: 18px;
  text-align: center;
  padding: 4px 0 12px;
}
.side-nav.collapsed .nav-list button {
  justify-content: center;
  padding: 10px 4px;
  font-size: 13px;
}
.nav-badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.45);
  display: inline-block;
}
.logout-compact {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.25);
  color: #cfe3de;
  border-radius: 5px;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 14px;
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
