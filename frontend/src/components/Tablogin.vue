<template>
  <div class="header-content">
    <div class="header-left">
      <span class="platform-info">遥感解译</span>
    </div>
    <div class="header-right">
      <span v-if="authenticated" class="user-pill">{{ username || "admin" }}</span>
      <el-button
        v-if="authenticated"
        plain
        class="logout-btn"
        @click="logout"
      >
        退出登录
      </el-button>
      <el-button
        v-if="minerEnabled"
        type="primary"
        class="miner-btn"
        @click="goToMiner"
      >
        <i class="icon-map" style="margin-right: 4px;" />
        监测地图
      </el-button>
    </div>
  </div>
</template>

<script>
import { legacyLogout, legacySession } from "@/api/auth";

export default {
  name: 'HeaderComponent',
  data() {
    return {
      authenticated: false,
      username: '',
    };
  },
  computed: {
    minerEnabled() {
      return process.env.VUE_APP_MINER_ENABLED === 'true';
    },
    minerUrl() {
      return process.env.VUE_APP_MINER_URL || 'http://localhost:4000';
    },
  },
  async mounted() {
    await this.refreshSession();
  },
  methods: {
    async refreshSession() {
      try {
        const response = await legacySession();
        const auth = response?.data?.data || {};
        this.authenticated = Boolean(auth.authenticated);
        this.username = auth.username || '';
      } catch (_) {
        this.authenticated = false;
        this.username = '';
      }
    },
    async logout() {
      try {
        await legacyLogout();
      } finally {
        window.location.assign('/login');
      }
    },
    goToMiner() {
      window.open(this.minerUrl, '_blank');
    },
  },
};
</script>
<style scoped>
.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0;
  font-family: Microsoft JhengHei UI, sans-serif;
  height: 60px;
  line-height: normal;
}

.header-left {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}

.platform-info {
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.6px;
  line-height: 1.2;
  white-space: nowrap;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-pill {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(22, 53, 47, 0.08);
  color: #24564c;
  font-size: 13px;
  font-weight: 600;
}

.logout-btn {
  border-radius: 10px;
}

.miner-btn {
  background: linear-gradient(135deg, rgba(78, 205, 196, 0.18), rgba(88, 166, 255, 0.18)) !important;
  border: 1px solid rgba(78, 205, 196, 0.35) !important;
  color: var(--text-primary) !important;
  font-weight: 600;
  border-radius: 10px;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
  backdrop-filter: blur(10px);
  height: 34px;
  display: inline-flex;
  align-items: center;
}

.miner-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
  border-color: rgba(78, 205, 196, 0.6) !important;
}

@media (max-width: 768px) {
  .header-content {
    padding: 0 10px;
  }
  
  .platform-info {
    font-size: 16px;
  }
}
</style>
