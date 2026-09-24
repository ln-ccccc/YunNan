<template>
  <aside class="sidebar left-sidebar">
    <div class="sidebar-header">
      <h2>数据概览</h2>
    </div>

    <div class="sidebar-content">
      <div v-if="dataLoadError" class="error-panel">
        {{ dataLoadError }}
      </div>

      <!-- 快捷操作区 -->
      <div class="action-panel" style="margin-bottom: 10px;">
        <button class="action-btn add-btn" @click="$emit('open-inference')">
          <i class="icon">+</i> 添加矿山与解译
        </button>
        <button class="action-btn trend-btn" @click="$emit('open-trend-report')">
          <i class="icon">↗</i> 趋势统计与导出
        </button>
      </div>

      <!-- 核心指标卡片 -->
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">矿山总数</div>
          <div class="metric-value text-cyan">{{ mineTotal }}</div>
          <div class="metric-unit">个</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">监测面积</div>
          <div class="metric-value text-blue">{{ (overviewArea / 10000).toFixed(2) }}</div>
          <div class="metric-unit">公顷</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">已治理</div>
          <div class="metric-value text-green">{{ treatedCount }}</div>
          <div class="metric-unit">个</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">治理率</div>
          <div class="metric-value text-yellow">{{ mineTotal ? ((treatedCount / mineTotal) * 100).toFixed(1) : 0 }}</div>
          <div class="metric-unit">%</div>
        </div>
      </div>

      <!-- 筛选控制区 -->
      <div class="control-panel glass-panel">
        <div class="panel-header">
          <h3>筛选查询</h3>
          <button class="reset-btn" @click="emitReset">重置</button>
        </div>
        <div class="filter-group">
          <label>所属州市</label>
          <select :value="filterCity" @change="$emit('update:filterCity', $event.target.value); emitApply()">
            <option value="">全部州市</option>
            <option v-for="city in cityOptions" :key="city" :value="city">{{ city }}</option>
          </select>
        </div>
        <div class="filter-group">
          <label>治理状态</label>
          <select :value="filterStatus" @change="$emit('update:filterStatus', $event.target.value); emitApply()">
            <option value="">全部状态</option>
            <option value="treated">已治理</option>
            <option value="untreated">未治理</option>
          </select>
        </div>
        <div class="filter-group">
          <label>开采方式</label>
          <select :value="filterMethod" @change="$emit('update:filterMethod', $event.target.value); emitApply()">
            <option value="">全部方式</option>
            <option v-for="method in miningMethodOptions" :key="method" :value="method">{{ method }}</option>
          </select>
        </div>
        <div class="search-box">
          <input type="text" :value="searchMineId" @input="$emit('update:searchMineId', $event.target.value)" placeholder="输入矿山名称或ID..." @keyup.enter="emitSearch">
          <button @click="emitSearch">查</button>
        </div>
      </div>

      <!-- 统计列表：与右栏同款横向条形（statBarOption 共享构建器） -->
      <div class="ranking-panel glass-panel">
        <div class="panel-header">
          <h3>修复方式 TOP5</h3>
          <span class="panel-unit">按矿山数 · 个</span>
        </div>
        <div
          v-if="(restorationMethodList || []).length"
          ref="rankChartRef"
          class="rank-chart-box"
        ></div>
        <p v-else class="panel-empty">暂无修复方式统计</p>
      </div>

      <!-- 开采方式统计：自右栏迁入，与修复方式同属矿山属性分布 -->
      <div class="ranking-panel glass-panel">
        <div class="panel-header">
          <h3>开采方式统计</h3>
          <span class="panel-unit">按矿山数 · 个</span>
        </div>
        <div
          v-if="(miningMethodList || []).length"
          ref="miningChartRef"
          class="rank-chart-box"
        ></div>
        <p v-else class="panel-empty">暂无开采方式统计</p>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { defineProps, defineEmits, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import * as echarts from 'echarts';
import { makeStatBarOption } from './statBarOption.js';

const props = defineProps({
  mineTotal: Number,
  overviewArea: Number,
  treatedCount: Number,
  untreatedCount: Number,
  restorationMethodList: Array,
  miningMethodList: Array,
  cityOptions: Array,
  miningMethodOptions: Array,
  filterCity: String,
  filterStatus: String,
  filterMethod: String,
  searchMineId: String,
  dataLoadError: String
});

const emit = defineEmits([
  'update:filterCity',
  'update:filterStatus',
  'update:filterMethod',
  'update:searchMineId',
  'apply-filters',
  'reset-filters',
  'search',
  'open-inference',
  'open-trend-report'
]);

const emitApply = () => emit('apply-filters');
const emitReset = () => emit('reset-filters');
const emitSearch = () => emit('search');

// 侧栏条形图插槽工厂：修复方式 TOP5 与开采方式统计共用挂载/重绘/销毁逻辑，
// 面板 flex-grow 时柱体随图幅自动加粗（ref 须以命名 ref 形式传入供模板绑定）
function createSidebarBarChart(elRef) {
  let inst = null;
  let observer = null;
  const render = (names, values) => {
    if (!elRef.value) return;
    if (!inst) {
      inst = echarts.init(elRef.value);
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => inst?.resize());
        observer.observe(elRef.value.parentElement);
      }
    }
    inst.setOption(makeStatBarOption({ names, values }), true);
  };
  const dispose = () => {
    observer?.disconnect();
    inst?.dispose();
    inst = null;
  };
  return { render, dispose };
}

const rankChartRef = ref(null);
const miningChartRef = ref(null);
const rankChart = createSidebarBarChart(rankChartRef);
const miningChart = createSidebarBarChart(miningChartRef);

const rankRows = () =>
  [...(props.restorationMethodList || [])]
    .sort((a, b) => (b.count || 0) - (a.count || 0))
    .slice(0, 5);
const miningRows = () => (props.miningMethodList || []).slice(0, 5);

watch(
  () => props.restorationMethodList,
  () => nextTick(() => rankChart.render(rankRows().map((item) => item.name), rankRows().map((item) => item.count))),
  { deep: true },
);

watch(
  () => props.miningMethodList,
  () => nextTick(() => miningChart.render(miningRows().map((item) => item.name), miningRows().map((item) => item.value))),
  { deep: true },
);

onMounted(() => {
  nextTick(() => {
    rankChart.render(rankRows().map((item) => item.name), rankRows().map((item) => item.count));
    miningChart.render(miningRows().map((item) => item.name), miningRows().map((item) => item.value));
  });
});

onBeforeUnmount(() => {
  rankChart.dispose();
  miningChart.dispose();
});
</script>

<style scoped>
.sidebar {
  width: 286px;
  flex-shrink: 0;
  /* 去除 backdrop-filter：其 GPU 合成层在内嵌浏览器/低配 GPU 上会触发整页渲染
     伪影（2026-09-09 实测截图平铺、截图超时），提高不透明度保持观感 */
  background: rgba(10, 25, 41, 0.97);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width 0.3s ease;
  overflow: hidden;
}

.sidebar-header {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  white-space: nowrap;
}
.sidebar-header h2 { font-size: 16px; margin: 0; color: #4ecdc4; flex: 1; text-align: center; }

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: opacity 0.2s;
}

.glass-panel {
  background: rgba(255,255,255,0.045);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 8px;
  padding: 10px;
}

.error-panel {
  padding: 8px 10px;
  color: #ffb3b3;
  background: rgba(255, 118, 117, 0.12);
  border: 1px solid rgba(255, 118, 117, 0.28);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.panel-header h3 {
  font-size: 14px;
  color: #fff;
  margin: 0;
  border-left: 3px solid #4ecdc4;
  padding-left: 8px;
}
.reset-btn {
  background: rgba(255,255,255,0.1);
  border: none;
  color: #8da3b6;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
  cursor: pointer;
}

.metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.metric-card {
  background: rgba(0,0,0,0.2);
  padding: 9px;
  border-radius: 6px;
  text-align: center;
}

.action-btn {
  width: 100%;
  padding: 8px;
  background: rgba(78, 205, 196, 0.1);
  border: 1px solid rgba(78, 205, 196, 0.3);
  color: #4ecdc4;
  border-radius: 6px;
  cursor: pointer;
  font-weight: bold;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.action-btn:hover {
  background: #4ecdc4;
  color: #0a1929;
}
.trend-btn {
  margin-top: 8px;
}
.action-btn .icon {
  font-style: normal;
  font-size: 16px;
}

.metric-label { font-size: 12px; color: #8da3b6; }
.metric-value { font-size: 18px; font-weight: bold; margin: 5px 0; }
.metric-unit { font-size: 10px; color: #666; }
.text-cyan { color: #4ecdc4; }
.text-blue { color: #24c1ff; }
.text-green { color: #00b894; }
.text-yellow { color: #f1c40f; }

.filter-group { margin-bottom: 10px; }
.filter-group label { display: block; font-size: 12px; color: #8da3b6; margin-bottom: 4px; }
.filter-group select {
  width: 100%;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.2);
  color: #fff;
  padding: 6px;
  border-radius: 4px;
}
.search-box { display: flex; gap: 5px; margin-top: 15px; }
.search-box input {
  flex: 1;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.2);
  color: #fff;
  padding: 6px;
  border-radius: 4px;
}
.search-box button { background: #4ecdc4; border: none; border-radius: 4px; cursor: pointer; }

/* 弹性面板：TOP5 条形图吃掉侧栏剩余高度，柱体随图幅加粗，底部不再留白；
   矮屏时 min-height 托底、整栏滚动 */
.ranking-panel { flex: 1 1 auto; display: flex; flex-direction: column; }
.rank-chart-box { width: 100%; flex: 1 1 auto; min-height: 150px; }
.panel-unit { font-size: 11px; color: #78918f; white-space: nowrap; }
.panel-empty { margin: 8px 0; font-size: 12px; color: #8da3b6; text-align: center; }
</style>
