<template>
  <div class="search-view">
    <header class="view-header">
      <div>
        <p class="kicker">查询搜索</p>
        <h1>项目与矿山检索</h1>
      </div>
    </header>

    <section class="view-body">
      <div class="search-row">
        <input
          v-model="keyword"
          type="text"
          placeholder="项目名称 / 区域，或矿山 ID（如 713）"
          @keyup.enter="runSearch"
        >
        <button class="primary-btn" type="button" @click="runSearch">查询</button>
      </div>

      <div v-if="searched">
        <h3>项目（{{ projectHits.length }}）</h3>
        <div v-for="row in projectHits" :key="'p' + row.id" class="hit-row project-hit" @click="openMap(row.id)">
          <strong>{{ row.name }}</strong>
          <span class="muted-text">{{ row.region || '未填区域' }} · {{ statusText(row.status) }} · 矿山 {{ row.mine_count || 0 }} 座 · 图斑 {{ row.feature_count || 0 }} 个</span>
        </div>
        <p v-if="!projectHits.length" class="empty-block">无匹配项目</p>

        <h3 v-if="mineHits.length">矿山（{{ mineHits.length }}）</h3>
        <div v-for="row in mineHits" :key="'m' + row.key" class="hit-row mine-hit" @click="openMap(row.projectId, row.fid)">
          <strong>矿山 {{ row.fid }}</strong>
          <span class="muted-text">项目：{{ row.projectName }} · 点击定位到地图</span>
        </div>
      </div>
      <p v-else class="empty-block">输入关键词检索；矿山 ID 将跨项目定位</p>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { ref } from 'vue';

const emit = defineEmits(['open-map']);

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const keyword = ref('');
const searched = ref(false);
const projectHits = ref([]);
const mineHits = ref([]);

const STATUS = { draft: '草稿', active: '进行中', completed: '已完成', archived: '已归档' };
const statusText = (s) => STATUS[s] || s || '未知';

const runSearch = async () => {
  searched.value = true;
  projectHits.value = [];
  mineHits.value = [];
  const kw = keyword.value.trim();
  if (!kw) return;
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/projects`);
    const items = res.data?.data?.items || [];
    // 项目模糊匹配：名称/区域
    projectHits.value = items.filter((p) =>
      !kw || String(p.name || '').includes(kw) || String(p.region || '').includes(kw)
    );
    // 矿山 ID 跨项目定位：纯数字时在所有项目的矿山绑定中匹配
    if (/^\d+$/.test(kw)) {
      const fid = Number(kw);
      const mineProjects = items.filter((p) => (p.mine_count || 0) > 0);
      const results = await Promise.all(
        mineProjects.map(async (p) => {
          try {
            const geo = await axios.get(
              `${MINER_API_BASE_URL}/api/projects/${p.id}/geojson`,
            );
            const features = geo.data?.data?.features || [];
            return features.some((f) => Number(f?.properties?.FID_1) === fid)
              ? { key: `${p.id}-${fid}`, projectId: p.id, fid, projectName: p.name }
              : null;
          } catch (_) {
            return null;
          }
        })
      );
      mineHits.value = results.filter(Boolean);
    }
  } catch (_) {
    /* 空结果保持 */
  }
};

const openMap = (projectId, fid = null) => {
  emit('open-map', projectId, fid);
};
</script>

<style scoped>
.search-view { max-width: 860px; margin: 0 auto; padding: 28px 24px; color: #264b45; }
.view-header { margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.search-row { display: flex; gap: 10px; margin-bottom: 16px; }
.search-row input {
  flex: 1; padding: 9px 12px; border-radius: 6px;
  border: 1px solid rgba(38, 75, 69, 0.3); font-size: 14px;
}
.primary-btn {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 8px 22px; cursor: pointer; font-size: 14px;
}
.primary-btn:hover { background: #264b45; }

h3 { font-size: 14px; color: #2f6f61; margin: 14px 0 8px; }

.hit-row {
  display: flex; flex-direction: column; gap: 2px;
  background: rgba(38, 75, 69, 0.04); border: 1px solid rgba(38, 75, 69, 0.1);
  border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; cursor: pointer;
}
.hit-row:hover { border-color: #2f6f61; }
.muted-text { color: #5a7d75; font-size: 12px; }
.empty-block { color: #7ba39a; font-size: 14px; text-align: center; padding: 18px 0; }
</style>
