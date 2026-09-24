<template>
  <aside class="sidebar right-sidebar" :class="{ collapsed }">
    <div class="sidebar-header">
      <button class="collapse-btn" @click="$emit('toggle')">
        {{ collapsed ? '◀' : '▶' }}
      </button>
      <h2>分析统计</h2>
    </div>

    <div class="sidebar-content" v-show="!collapsed">
      <div class="metric-grid glass-panel">
        <div class="metric-card">
          <div class="metric-label">变化面积</div>
          <div class="metric-value text-cyan">{{ formatKm2(changeAreaStats?.total_changed_km2) }}</div>
          <div class="metric-unit">km²</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">覆盖率</div>
          <div class="metric-value text-blue">{{ formatCoverage(changeAreaStats?.coverage_ratio) }}</div>
          <div class="metric-unit">%</div>
        </div>
        <div class="metric-card full-width">
          <div class="metric-label">缺失矿山数</div>
          <div class="metric-value text-yellow">{{ changeAreaStats?.missing_mine_count ?? 0 }}</div>
          <div class="metric-unit">无数据</div>
        </div>
      </div>

      <div class="chart-panel glass-panel grow">
        <div class="panel-header"><h3>治理状态分布</h3></div>
        <div ref="pieChartRef" class="chart-box pie-chart-box"></div>
      </div>

      <div class="chart-panel glass-panel grow">
        <div class="panel-header">
          <h3>修复后地类</h3>
          <span class="panel-unit">按矿山数 · 个</span>
        </div>
        <div
          v-if="(landTypeList || []).length"
          ref="landChartRef"
          class="chart-box"
          :style="{ minHeight: landChartMinHeight }"
        ></div>
        <p v-else class="panel-empty">暂无地类统计</p>
        <p v-if="(landTypeList || []).length" class="panel-note">矿山涉及多个地类时按地类分别计入</p>
      </div>

      <div class="chart-panel glass-panel grow">
        <div class="panel-header">
          <h3>开采方式统计</h3>
          <span class="panel-unit">按矿山数 · 个</span>
        </div>
        <div ref="barChartRef" class="chart-box"></div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { defineProps, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import * as echarts from 'echarts';
import { makeStatBarOption } from './statBarOption.js';

const props = defineProps({
  collapsed: Boolean,
  treatedCount: Number,
  untreatedCount: Number,
  landTypeList: Array,
  miningMethodList: Array,
  changeAreaStats: {
    type: Object,
    default: () => ({
      total_changed_km2: 0,
      valid_mine_count: 0,
      missing_mine_count: 0,
      coverage_ratio: 0
    })
  }
});

const formatKm2 = (value) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(3) : '--';
};

const formatCoverage = (value) => {
  const num = Number(value);
  return Number.isFinite(num) ? (num * 100).toFixed(1) : '--';
};

const pieChartRef = ref(null);
const barChartRef = ref(null);
const landChartRef = ref(null);
let pieChartInst = null;
let barChartInst = null;
let landChartInst = null;
let chartResizeObserver = null;

const resizeCharts = () => {
  pieChartInst?.resize();
  barChartInst?.resize();
  landChartInst?.resize();
};

// 面板最小高度随行数走（每行约 26px + 轴留白），钳在 180~380px；
// 面板本身随侧栏弹性伸展（flex-grow），大屏下图表吃掉剩余高度而不是留白
const landChartMinHeight = computed(() => {
  const rows = Math.min(12, (props.landTypeList || []).length);
  return `${Math.min(380, Math.max(180, rows * 26 + 64))}px`;
});

const landChartRows = () => (props.landTypeList || []).slice(0, 12);

const miningBarRows = () => {
  const list = (props.miningMethodList || []).slice(0, 5);
  return {
    names: list.map((item) => item.name),
    values: list.map((item) => item.value),
  };
};

const getPieData = () => ([
  { value: props.treatedCount, name: '已治理', itemStyle: { color: '#00b894' } },
  { value: props.untreatedCount, name: '未治理', itemStyle: { color: '#ff7675' } }
]);

const initPieChart = () => {
  if (!pieChartRef.value) return;
  pieChartInst = echarts.init(pieChartRef.value);
  pieChartInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    legend: {
      bottom: 2,
      left: 'center',
      itemWidth: 14,
      itemHeight: 10,
      textStyle: { color: '#fff', fontSize: 12 }
    },
    series: [
      {
        name: '治理状态',
        type: 'pie',
        radius: ['38%', '62%'],
        center: ['50%', '42%'],
        data: getPieData(),
        label: { show: false },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0, 0, 0, 0.5)'
          }
        }
      }
    ]
  });
};

const initBarChart = () => {
  if (!barChartRef.value) return;
  barChartInst = echarts.init(barChartRef.value);
  const rows = miningBarRows();
  barChartInst.setOption(makeStatBarOption({ names: rows.names, values: rows.values }));
};

const initLandChart = () => {
  if (!landChartRef.value) return;
  landChartInst = echarts.init(landChartRef.value);
  const rows = landChartRows();
  landChartInst.setOption(makeStatBarOption({ names: rows.map((item) => item.name), values: rows.map((item) => item.value) }));
};

const updateCharts = () => {
  if (pieChartInst) {
    pieChartInst.setOption({
      series: [{ data: getPieData() }]
    });
  }

  if (barChartInst) {
    const rows = miningBarRows();
    barChartInst.setOption(makeStatBarOption({ names: rows.names, values: rows.values }), true);
  }

  nextTick(() => {
    // 地类面板是 v-if 门控：数据晚到时 ref 才出现，这里补初始化
    if (!landChartInst && landChartRef.value) {
      initLandChart();
    }
    if (landChartInst) {
      const rows = landChartRows();
      landChartInst.setOption(makeStatBarOption({ names: rows.map((item) => item.name), values: rows.map((item) => item.value) }), true);
    }
    resizeCharts();
  });
};

watch(() => [props.treatedCount, props.untreatedCount], updateCharts);
watch(() => props.miningMethodList, updateCharts, { deep: true });
watch(() => props.landTypeList, updateCharts, { deep: true });
watch(() => props.collapsed, () => {
  nextTick(() => {
    resizeCharts();
  });
});

onMounted(() => {
  nextTick(() => {
    initPieChart();
    initBarChart();
    initLandChart();
    resizeCharts();
  });

  window.addEventListener('resize', resizeCharts);

  if (typeof ResizeObserver !== 'undefined') {
    chartResizeObserver = new ResizeObserver(() => {
      resizeCharts();
    });
    if (pieChartRef.value?.parentElement) {
      chartResizeObserver.observe(pieChartRef.value.parentElement);
    }
    if (barChartRef.value?.parentElement) {
      chartResizeObserver.observe(barChartRef.value.parentElement);
    }
    if (landChartRef.value?.parentElement) {
      chartResizeObserver.observe(landChartRef.value.parentElement);
    }
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts);
  chartResizeObserver?.disconnect();
  pieChartInst?.dispose();
  barChartInst?.dispose();
  landChartInst?.dispose();
});
</script>

<style scoped>
.sidebar {
  width: 286px;
  flex-shrink: 0;
  /* 同 LeftSidebar：移除 backdrop-filter 避免 GPU 合成伪影 */
  background: rgba(10, 25, 41, 0.97);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width 0.3s ease;
  overflow: hidden;
}

.right-sidebar {
  border-left: 1px solid var(--border-color);
  border-right: none;
}

.sidebar.collapsed {
  width: 40px;
}

.sidebar.collapsed .sidebar-content {
  opacity: 0;
  pointer-events: none;
}

.sidebar-header {
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  white-space: nowrap;
}

.sidebar-header h2 {
  font-size: 16px;
  margin: 0;
  color: #4ecdc4;
  flex: 1;
  text-align: center;
}

.collapse-btn {
  background: none;
  border: none;
  color: #8da3b6;
  cursor: pointer;
  font-size: 12px;
  padding: 5px;
}

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
  background: rgba(255, 255, 255, 0.045);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 10px;
}

.panel-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.panel-header h3 {
  font-size: 14px;
  color: #fff;
  margin: 0 0 10px 0;
  border-left: 3px solid #4ecdc4;
  padding-left: 8px;
}

.panel-unit {
  font-size: 11px;
  color: #78918f;
  white-space: nowrap;
}

.panel-note {
  margin: 6px 0 0;
  font-size: 11px;
  color: #78918f;
}

.panel-empty {
  margin: 8px 0;
  font-size: 12px;
  color: #8da3b6;
  text-align: center;
}

/* 弹性面板：大屏下三个图表均分侧栏剩余高度，图表容器吃满面板（ResizeObserver
   已监听面板尺寸变化自动重绘），底部不再留白；矮屏时被 min-height 托底、整栏滚动 */
.chart-panel.grow {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}

.chart-box {
  width: 100%;
  flex: 1 1 auto;
  min-height: 150px;
}

.pie-chart-box {
  min-height: 200px;
}

.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.metric-card {
  background: rgba(0, 0, 0, 0.2);
  padding: 9px;
  border-radius: 6px;
  text-align: center;
}

.metric-card.full-width {
  grid-column: 1 / span 2;
}

.metric-label {
  font-size: 12px;
  color: #8da3b6;
}

.metric-value {
  font-size: 18px;
  font-weight: bold;
  margin: 5px 0;
}

.metric-unit {
  font-size: 10px;
  color: #666;
}

.text-cyan {
  color: #4ecdc4;
}

.text-blue {
  color: #24c1ff;
}

.text-yellow {
  color: #f1c40f;
}
</style>
