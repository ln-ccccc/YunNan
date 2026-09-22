<template>
  <div class="settings-view">
    <header class="view-header">
      <div>
        <p class="kicker">系统设置</p>
        <h1>平台信息</h1>
      </div>
    </header>

    <section class="view-body">
      <div class="info-grid">
        <article class="info-card">
          <span>平台</span>
          <strong>矿山生态修复智能监测平台</strong>
        </article>
        <article class="info-card">
          <span>主控台版本</span>
          <strong>M1–M4（2026-09-22）</strong>
        </article>
        <article class="info-card">
          <span>当前用户</span>
          <strong>{{ username || 'admin' }}</strong>
        </article>
        <article class="info-card">
          <span>项目总数</span>
          <strong>{{ stats?.project_total ?? '—' }}</strong>
        </article>
      </div>

      <h3>功能模块状态</h3>
      <ul class="module-list">
        <li v-for="item in modules" :key="item.key">
          <span>{{ item.label }}</span>
          <span :class="item.implemented ? 'ok' : 'pending'">
            {{ item.implemented ? '已上线' : '规划中（' + item.milestone + '）' }}
          </span>
        </li>
      </ul>

      <p class="muted-text note">
        口令修改与多用户权限属后期范围（本期不做 RBAC）；运行环境与部署配置由管理员在服务器侧维护。
      </p>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { onMounted, ref } from 'vue';

defineProps({ username: { type: String, default: '' } });

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const stats = ref(null);

const modules = [
  { key: 'projects', label: '项目管理', implemented: true },
  { key: 'imagery', label: '影像管理', implemented: true },
  { key: 'interpretation', label: '智能解译', implemented: false, milestone: '工作台内发起' },
  { key: 'editing', label: '图斑编辑', implemented: true },
  { key: 'data', label: '数据管理', implemented: false, milestone: '工作台导出/快照' },
  { key: 'search', label: '查询搜索', implemented: true },
  { key: 'settings', label: '系统设置', implemented: true },
];

onMounted(async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/stats/overview`);
    stats.value = res.data?.data || null;
  } catch (_) {
    stats.value = null;
  }
});
</script>

<style scoped>
.settings-view { max-width: 720px; margin: 0 auto; padding: 28px 24px; color: #264b45; }
.view-header { margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 14px; }
.info-card {
  background: rgba(38, 75, 69, 0.05); border: 1px solid rgba(38, 75, 69, 0.12);
  border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px;
}
.info-card span { font-size: 12px; color: #5a7d75; }
.info-card strong { font-size: 15px; }

h3 { font-size: 14px; color: #2f6f61; margin: 14px 0 8px; }

.module-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.module-list li {
  display: flex; justify-content: space-between; font-size: 13px;
  background: rgba(38, 75, 69, 0.04); border-radius: 6px; padding: 8px 12px;
}
.ok { color: #00a383; }
.pending { color: #b8860b; }

.muted-text { color: #5a7d75; font-size: 12px; }
.note { margin-top: 14px; }
</style>
