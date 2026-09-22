<template>
  <transition name="fade">
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-content glass-panel">
        <div class="modal-header">
          <h3>{{ mineData.name }}</h3>
          <button class="close-btn" @click="$emit('close')">×</button>
        </div>
        <div class="modal-body">
          <div class="info-grid">
            <div class="info-item"><span class="label">矿山ID:</span> <span class="val">{{ mineData.mine_id }}</span></div>
            <div class="info-item"><span class="label">面积:</span> <span class="val">{{ mineData.area ? Number(mineData.area).toFixed(2) : '暂无' }} m²</span></div>
            <div class="info-item"><span class="label">状态:</span>
              <span class="status-tag" :class="mineData.status_normalized">
                {{ mineData.status_raw || '未知' }}
              </span>
            </div>
            <div class="info-item"><span class="label">坐标:</span> <span class="val">{{ (mineData.center_lat||0).toFixed(4) }}, {{ (mineData.center_lng||0).toFixed(4) }}</span></div>
          </div>

          <div class="source-badge" v-if="selectedTab === 'Change Matrix' || selectedTab === 'Classification'">
            Source: {{ getMatrixSourceText() }}
          </div>

          <div class="tabs">
            <button v-for="tab in ['NDVI', 'NDBI', 'NDWI', 'NDSI', 'Change Matrix', 'Classification', 'Original']"
              :key="tab"
              :class="{ active: selectedTab === tab }"
              @click="$emit('tab-change', tab)">
              {{ tab === 'Change Matrix' ? '变化矩阵' : (tab === 'Classification' ? '地物分类' : (tab === 'Original' ? '原始影像' : tab)) }}
            </button>
          </div>

          <template v-if="selectedTab === 'Change Matrix'">
            <div v-if="changeMatrixData && changeMatrixData.has_change_matrix" class="matrix-container">
              <table class="confusion-matrix">
                <thead>
                  <tr>
                    <th class="corner-cell">
                      <div class="corner-old">{{ matrixYearLabels.old }}</div>
                      <div class="corner-new">{{ matrixYearLabels.new }}</div>
                    </th>
                    <th v-for="h in changeMatrixData.headers" :key="h">{{ formatLabel(h) }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in changeMatrixData.matrix" :key="row.label">
                    <td class="row-label">{{ formatLabel(row.label) }}</td>
                    <td v-for="(val, idx) in row.values" :key="idx"
                        :style="{ backgroundColor: getHeatmapColor(val) }"
                        class="matrix-cell">
                      {{ val.toFixed(1) }}%
                    </td>
                  </tr>
                </tbody>
              </table>
              <div class="matrix-legend">
                <span>0%</span>
                <div class="legend-bar"></div>
                <span>100%</span>
              </div>
            </div>
            <div v-else class="no-data">暂无变化矩阵数据</div>
          </template>

          <template v-else-if="selectedTab === 'Classification'">
            <template v-if="traceabilityYears.length">
              <div class="trace-container">
                <div v-for="row in traceabilityYears" :key="row.key" class="trace-year-card">
                  <div class="trace-year-head">
                    <strong>{{ row.year ?? '未知年份' }}</strong>
                    <span class="trace-meta">图斑 {{ row.featureCount }} 个 · {{ row.vectorStatus === 'ready' ? '矢量就绪' : (row.vectorStatus || '状态未知') }} · 修订 {{ row.revisionCount }} 次</span>
                  </div>
                  <div class="trace-images">
                    <div class="trace-img-box">
                      <img v-if="row.resultImage" :src="row.resultImage" class="trace-img" :alt="`${row.year} 成果`" />
                      <span class="trace-img-label">解译成果</span>
                    </div>
                    <div class="trace-img-box">
                      <img v-if="row.sourceImage" :src="row.sourceImage" class="trace-img" :alt="`${row.year} 原图`" />
                      <span class="trace-img-label">原始影像</span>
                    </div>
                  </div>
                  <div v-if="row.classRatio" class="trace-ratio">
                    <span v-for="(val, name) in row.classRatio" :key="name" class="trace-ratio-chip" :class="{ zero: val < 0.5 }">
                      {{ formatRatioClass(name) }} {{ val.toFixed(1) }}%
                    </span>
                  </div>
                  <div v-if="row.revisions.length" class="trace-revisions">
                    <span v-for="rev in row.revisions.slice(0, 4)" :key="rev.key" class="trace-rev-chip">
                      {{ rev.label }} · {{ rev.actor }} · {{ rev.action }}
                    </span>
                  </div>
                </div>
              </div>
              <div v-if="ratioSeriesRows.length" class="ratio-series">
                <div class="ratio-series-title">历年地类占比（%）</div>
                <table class="ratio-table">
                  <thead>
                    <tr>
                      <th>地类</th>
                      <th v-for="year in traceabilityYears.map((y) => y.year).slice().reverse()" :key="year">{{ year }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in ratioSeriesRows" :key="row.name">
                      <td class="row-label">{{ row.label }}</td>
                      <td v-for="(val, i) in row.values" :key="i">{{ val.toFixed(1) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
            <div v-else-if="classificationItems.length" class="classification-container">
              <div v-for="item in classificationItems" :key="item.key" class="class-image-box">
                <div class="class-title">{{ item.title }}（{{ item.year ?? '未知年份' }}）</div>
                <div class="class-image-wrapper">
                  <img :src="item.url" :alt="`${item.key} classification`" class="class-image" />
                  <div class="class-year-label">{{ item.year ?? '未知' }}</div>
                </div>
              </div>
              <div v-if="classificationItems.length === 1" class="no-data">暂无可比较的前期分类结果</div>
            </div>
            <div v-else class="no-data">暂无地物分类结果</div>
          </template>

          <template v-else-if="selectedTab === 'Original'">
            <div v-if="originalImageryItems.length" class="original-imagery-container">
              <div v-for="item in originalImageryItems" :key="item.key" class="imagery-row">
                <div class="imagery-main">
                  <div class="imagery-title">{{ item.title }}</div>
                  <div class="imagery-meta">
                    <span class="imagery-name">{{ item.filename }}</span>
                    <span>{{ item.sizeText }}</span>
                    <span>推理时间: {{ item.timeText }}</span>
                  </div>
                </div>
                <div class="imagery-side">
                  <span class="imagery-status">{{ item.statusText }}</span>
                  <button
                    v-if="item.downloadable"
                    class="imagery-download-btn"
                    @click="$emit('download-original', item)"
                  >下载</button>
                </div>
              </div>
            </div>
            <div v-else class="no-data">暂无原始影像记录（该矿山未参与过项目推理）</div>
          </template>

          <template v-else>
            <div v-if="currentIndexData && currentIndexData.available === false" class="no-data">
              {{ currentIndexData.message || `${selectedTab} 指数源文件缺失` }}
            </div>
            <div v-else-if="!currentIndexData || !(currentIndexData.data || []).length" class="no-data">
              暂无 {{ selectedTab }} 历史指标数据
            </div>
            <template v-else>
              <div class="trend-stats">
                <div class="stat-box">
                  <span class="label">均值</span>
                  <span class="val">{{ formatMaybeNumber(currentIndexData?.mean, 3) }}</span>
                </div>
                <div class="stat-box">
                  <span class="label">趋势斜率</span>
                  <span class="val" :class="getTrendClass(currentIndexData?.trend)">
                    {{ formatTrend(currentIndexData?.trend) }}
                  </span>
                </div>
                <div class="stat-box">
                  <span class="label">MK检验</span>
                  <span class="val">{{ currentIndexData?.mk_trend }}</span>
                </div>
              </div>

              <div class="chart-container" ref="trendChartRef"></div>
            </template>
          </template>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed, defineProps, defineEmits, ref, watch, nextTick, onUnmounted } from 'vue';
import * as echarts from 'echarts';
import {
  buildClassificationItems,
  buildMatrixYearLabels,
  buildOriginalImageryItems,
  buildRatioSeriesRows,
  buildTraceabilityYears,
  formatRatioClass,
} from './mineDetailPresentation.js';

// 切换项目重建组件时释放 echarts 实例（与 RightSidebar 的清理模式对齐）
onUnmounted(() => {
  trendChartInst?.dispose();
  trendChartInst = null;
});

const props = defineProps({
  visible: Boolean,
  mineData: Object,
  indicesData: Object,
  changeMatrixData: Object,
  originalImageryData: Object,
  traceabilityData: Object,
  selectedTab: String,
  formatMaybeNumber: Function,
  formatTrend: Function,
  getTrendClass: Function
});

const emit = defineEmits(['close', 'tab-change', 'download-original']);

const trendChartRef = ref(null);
let trendChartInst = null;
const currentIndexData = computed(() => props.indicesData?.[props.selectedTab?.toLowerCase()] || null);
const classificationItems = computed(() => buildClassificationItems(props.changeMatrixData));
const matrixYearLabels = computed(() => buildMatrixYearLabels(props.changeMatrixData));
const originalImageryItems = computed(() => buildOriginalImageryItems(props.originalImageryData));
const traceabilityYears = computed(() => buildTraceabilityYears(props.traceabilityData));
const ratioSeriesRows = computed(() => buildRatioSeriesRows(props.traceabilityData));

const formatLabel = (label) => {
  if (!label) return '';
  const nameMap = {
    grassland: '草地',
    forest: '林地',
    building: '建筑',
    road: '道路',
    bareground: '裸地',
    water: '水体'
  };
  const key = label.replace(/^T[12]_/, '').toLowerCase();
  return nameMap[key] || key;
};

const getMatrixSourceText = () => {
  const source = props.changeMatrixData && props.changeMatrixData.data_source;
  if (source === 'historical_output') return '历史输出';
  if (source === 'pixel_resolution') return '像元分辨率估算';
  if (source === 'project_inference') return '项目推理';
  return '无数据';
};

const getHeatmapColor = (val) => {
  const opacity = Math.min(val / 100, 1);
  return `rgba(78, 205, 196, ${opacity * 0.8})`;
};

const renderTrendChart = () => {
  if (props.selectedTab === 'Change Matrix' || props.selectedTab === 'Classification') return;
  if (!trendChartRef.value) return;
  const dataset = currentIndexData.value;
  if (!dataset || dataset.available === false || !(dataset.data || []).length) {
    trendChartInst?.dispose();
    trendChartInst = null;
    return;
  }
  if (trendChartInst) trendChartInst.dispose();
  trendChartInst = echarts.init(trendChartRef.value);

  const key = props.selectedTab.toLowerCase();
  const data = dataset?.data || [];

  const years = data.map(d => d.year);
  const values = data.map(d => d.value);

  const colorMap = { ndvi: '#00b894', ndbi: '#fdcb6e', ndwi: '#0984e3', ndsi: '#e17055' };
  const color = colorMap[key] || '#00b894';

  trendChartInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { top: 30, bottom: 20, left: 40, right: 20 },
    xAxis: { type: 'category', data: years, axisLine: { lineStyle: { color: '#666' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }, axisLine: { lineStyle: { color: '#666' } } },
    series: [{
      data: values,
      type: 'line',
      smooth: true,
      lineStyle: { color: color, width: 3 },
      itemStyle: { color: color },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: color + '80' }, { offset: 1, color: color + '00' }])
      }
    }]
  });
};

watch(() => [props.visible, props.selectedTab, props.indicesData], () => {
  if (props.visible) {
    nextTick(() => renderTrendChart());
  }
}, { deep: true });
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.6);
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-content {
  width: 600px;
  background: #0a1929;
  border: 1px solid #4ecdc4;
  box-shadow: 0 0 30px rgba(78, 205, 196, 0.2);
}
.modal-header { display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; margin-bottom: 15px; }
.close-btn { background: none; border: none; color: #fff; font-size: 24px; cursor: pointer; }

.glass-panel {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  padding: 12px;
}

.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
.info-item { display: flex; justify-content: space-between; border-bottom: 1px dashed rgba(255,255,255,0.1); padding-bottom: 5px; }
.label { color: #8da3b6; font-size: 13px; }
.val { font-weight: bold; }
.status-tag { padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.status-tag.treated { background: rgba(0, 184, 148, 0.2); color: #00b894; }
.status-tag.untreated { background: rgba(255, 118, 117, 0.2); color: #ff7675; }

.source-badge {
  margin-bottom: 10px;
  color: #8da3b6;
  font-size: 12px;
}

.tabs { display: flex; gap: 10px; margin-bottom: 15px; }
.tabs button {
  flex: 1;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.1);
  color: #fff;
  padding: 8px;
  cursor: pointer;
}
.tabs button.active { background: #4ecdc4; color: #000; border-color: #4ecdc4; }

.trend-stats { display: flex; justify-content: space-around; margin-bottom: 15px; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 6px; }
.stat-box { display: flex; flex-direction: column; align-items: center; }
.stat-box .val { font-size: 16px; margin-top: 5px; }
.text-red { color: #ff7675; }
.text-green { color: #00b894; }

.chart-container { height: 250px; width: 100%; }

.matrix-container {
  margin-top: 10px;
  background: rgba(0, 0, 0, 0.2);
  padding: 15px;
  border-radius: 8px;
  overflow-x: auto;
}

.confusion-matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  table-layout: fixed;
}

.confusion-matrix th, .confusion-matrix td {
  padding: 8px 4px;
  text-align: center;
  border: 1px solid rgba(255, 255, 255, 0.05);
}

.confusion-matrix th {
  color: #8da3b6;
  font-weight: normal;
  background: rgba(255, 255, 255, 0.03);
}

.corner-cell {
  position: relative;
  min-width: 80px;
  height: 56px;
  background: rgba(255, 255, 255, 0.03);
  overflow: hidden;
}

.corner-cell::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, transparent 48%, rgba(255, 255, 255, 0.35) 50%, transparent 52%);
  pointer-events: none;
}

.corner-old,
.corner-new {
  position: absolute;
  font-size: 11px;
  color: #8da3b6;
}

.corner-old {
  top: 4px;
  left: 6px;
  text-align: left;
}

.corner-new {
  bottom: 4px;
  right: 6px;
  text-align: right;
}

.row-label {
  color: #8da3b6;
  text-align: left !important;
  width: 60px;
}

.matrix-cell {
  transition: transform 0.2s;
  cursor: default;
  color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,0.5);
}

.matrix-cell:hover {
  transform: scale(1.05);
  z-index: 1;
}

.matrix-legend {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 15px;
  font-size: 12px;
  color: #8da3b6;
  justify-content: center;
}

.legend-bar {
  width: 200px;
  height: 10px;
  background: linear-gradient(to right, rgba(78, 205, 196, 0), rgba(78, 205, 196, 0.8));
  border-radius: 5px;
}

.no-data {
  text-align: center;
  padding: 40px;
  color: #8da3b6;
}

.classification-container {
  margin-top: 10px;
  display: flex;
  gap: 16px;
  justify-content: space-between;
}

.class-image-box {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 8px;
  padding: 10px;
}

.class-title {
  font-size: 13px;
  margin-bottom: 6px;
  color: #8da3b6;
}

.class-image-wrapper {
  position: relative;
  width: 100%;
}

.class-image {
  width: 100%;
  border-radius: 6px;
  object-fit: cover;
}

.class-year-label {
  position: absolute;
  left: 10px;
  bottom: 10px;
  padding: 2px 8px;
  font-size: 12px;
  color: #000;
  background: rgba(255, 255, 255, 0.8);
  border-radius: 4px;
}

.trace-container {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 420px;
  overflow-y: auto;
}

.trace-year-card {
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  padding: 10px 12px;
}

.trace-year-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.trace-meta { font-size: 12px; color: #8da3b6; }

.trace-images { display: flex; gap: 10px; }
.trace-img-box { flex: 1; min-width: 0; position: relative; }
.trace-img { width: 100%; border-radius: 6px; display: block; }
.trace-img-label {
  position: absolute; left: 8px; bottom: 6px;
  font-size: 11px; color: #000;
  background: rgba(255, 255, 255, 0.8); border-radius: 4px; padding: 1px 6px;
}

.trace-ratio { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.trace-ratio-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: rgba(78, 205, 196, 0.15); color: #4ecdc4;
}
.trace-ratio-chip.zero { color: #5a7d75; background: rgba(255, 255, 255, 0.05); }

.trace-revisions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.trace-rev-chip { font-size: 11px; color: #8da3b6; background: rgba(255, 255, 255, 0.05); border-radius: 4px; padding: 2px 6px; }

.ratio-series { margin-top: 14px; }
.ratio-series-title { font-size: 13px; color: #4ecdc4; margin-bottom: 6px; }
.ratio-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.ratio-table th, .ratio-table td { border: 1px solid rgba(255, 255, 255, 0.06); padding: 4px 8px; text-align: center; color: #cfe3de; }
.ratio-table .row-label { color: #8da3b6; text-align: left; }

.original-imagery-container {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 360px;
  overflow-y: auto;
}

.imagery-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  padding: 10px 12px;
}

.imagery-main { flex: 1; min-width: 0; }
.imagery-title { font-size: 14px; margin-bottom: 4px; }
.imagery-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #8da3b6; }
.imagery-name { word-break: break-all; }

.imagery-side { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.imagery-status { font-size: 12px; color: #8da3b6; }
.imagery-download-btn {
  background: rgba(78, 205, 196, 0.15);
  border: 1px solid #4ecdc4;
  color: #4ecdc4;
  border-radius: 4px;
  padding: 4px 14px;
  cursor: pointer;
  font-size: 12px;
}
.imagery-download-btn:hover { background: #4ecdc4; color: #000; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
