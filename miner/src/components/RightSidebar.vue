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

      <div class="chart-panel glass-panel">
        <div class="panel-header"><h3>治理状态分布</h3></div>
        <div ref="pieChartRef" class="chart-box pie-chart-box"></div>
      </div>

      <div class="chart-panel glass-panel">
        <div class="panel-header"><h3>修复后地类</h3></div>
        <div class="land-type-list">
          <div class="land-item" v-for="(item, idx) in landTypeList" :key="idx">
            <div class="land-info">
              <span class="land-name">{{ item.name }}</span>
              <span class="land-val">{{ item.value }}</span>
            </div>
            <div class="progress-bg">
              <div
                class="progress-fill"
                :style="{ width: Math.min(100, (item.value / (landTypeList[0]?.value || 1)) * 100) + '%' }"
              ></div>
            </div>
          </div>
        </div>
      </div>

      <div class="chart-panel glass-panel">
        <div class="panel-header"><h3>开采方式统计</h3></div>
        <div ref="barChartRef" class="chart-box"></div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { defineProps, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import * as echarts from 'echarts';

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
let pieChartInst = null;
let barChartInst = null;
let chartResizeObserver = null;

const resizeCharts = () => {
  pieChartInst?.resize();
  barChartInst?.resize();
};

const getPieData = () => ([
  { value: props.treatedCount, name: '已治理', itemStyle: { color: '#2fa98a' } },
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
  const list = (props.miningMethodList || []).slice(0, 5);

  barChartInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '4%', bottom: '3%', top: '3%', containLabel: true },
    xAxis: { type: 'value', splitLine: { show: false }, axisLabel: { color: '#ccc' } },
    yAxis: {
      type: 'category',
      data: list.map((item) => item.name),
      axisLabel: { color: '#ccc', width: 80, overflow: 'truncate' }
    },
    series: [
      {
        type: 'bar',
        data: list.map((item) => item.value),
        itemStyle: {
          color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
            { offset: 0, color: '#2fa98a' },
            { offset: 1, color: '#74b9ff' }
          ])
        },
        barWidth: '60%'
      }
    ]
  });
};

const updateCharts = () => {
  if (pieChartInst) {
    pieChartInst.setOption({
      series: [{ data: getPieData() }]
    });
  }

  if (barChartInst) {
    const list = (props.miningMethodList || []).slice(0, 5);
    barChartInst.setOption({
      yAxis: { data: list.map((item) => item.name) },
      series: [{ data: list.map((item) => item.value) }]
    });
  }

  nextTick(() => {
    resizeCharts();
  });
};

watch(() => [props.treatedCount, props.untreatedCount], updateCharts);
watch(() => props.miningMethodList, updateCharts, { deep: true });
watch(() => props.collapsed, () => {
  nextTick(() => {
    resizeCharts();
  });
});

onMounted(() => {
  nextTick(() => {
    initPieChart();
    initBarChart();
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
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts);
  chartResizeObserver?.disconnect();
  pieChartInst?.dispose();
  barChartInst?.dispose();
});
</script>

<style scoped>
.sidebar {
  width: 286px;
  /* 同 LeftSidebar：移除 backdrop-filter 避免 GPU 合成伪影 */
  background: rgba(19, 20, 22, 0.97);
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
  color: #2fa98a;
  flex: 1;
  text-align: center;
}

.collapse-btn {
  background: none;
  border: none;
  color: #9f9fa4;
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

.panel-header h3 {
  font-size: 14px;
  color: #fff;
  margin: 0 0 10px 0;
  border-left: 3px solid #2fa98a;
  padding-left: 8px;
}

.chart-box {
  width: 100%;
  height: 168px;
}

.pie-chart-box {
  height: clamp(190px, 24vh, 230px);
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
  color: #9f9fa4;
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
  color: #2fa98a;
}

.text-blue {
  color: #2fa98a;
}

.text-yellow {
  color: #f1c40f;
}

.land-type-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.land-item {
  font-size: 12px;
}

.land-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 2px;
}

.progress-bg {
  height: 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #6c5ce7, #a29bfe);
  border-radius: 3px;
}
</style>
