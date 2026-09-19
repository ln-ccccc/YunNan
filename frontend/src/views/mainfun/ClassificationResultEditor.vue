<template>
  <section class="classification-editor">
    <div class="editor-header">
      <div>
        <p class="eyebrow">分类成果 · 矢量审校</p>
        <h1>地物分类矢量编辑</h1>
        <p class="subtitle">
          <template v-if="result">
            矿山 FID {{ result.mine_fid }} · {{ result.year }} 年 · 当前版本 V{{ result.current_revision_no ?? '-' }}
          </template>
          <template v-else>仅编辑当前项目内、已生成的分类矢量成果</template>
        </p>
      </div>
      <div class="header-actions">
        <el-button plain @click="goBack">返回分类结果</el-button>
        <el-button :loading="loading" plain @click="reloadResult">重新加载</el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      class="editor-alert"
      type="error"
      :closable="false"
      :title="loadError"
      show-icon
    />

    <el-skeleton v-else-if="loading" :rows="8" animated />

    <template v-else-if="result">
      <el-alert
        v-if="result.vector_status === 'vector_failed'"
        class="editor-alert"
        type="warning"
        :closable="false"
        :title="`该成果未生成可编辑矢量：${result.vector_error || '自动矢量化失败'}`"
        description="原始分类图仍可在结果列表中查看；当前版本不允许新建或保存矢量。"
        show-icon
      />

      <div v-else class="editor-toolbar">
        <div class="toolbar-state">
          <span class="state-dot" :class="{ dirty: isDirty }" />
          {{ isDirty ? '存在未保存编辑' : '当前版本已保存' }}
          <span v-if="selectedDrawIds.length" class="selection-count">已选择 {{ selectedDrawIds.length }} 个面</span>
        </div>
        <div class="toolbar-actions">
          <el-button :disabled="!isDirty || saving" plain @click="resetDrawing">撤销本地修改</el-button>
          <el-button :loading="exporting" plain @click="downloadCurrent">导出当前 GeoJSON</el-button>
          <el-button type="primary" :disabled="!isDirty" :loading="saving" @click="saveDrawing">保存新版本</el-button>
        </div>
      </div>

      <div v-if="result.vector_status !== 'vector_failed'" class="editor-workspace">
        <aside class="editor-panel class-panel">
          <div class="panel-heading">
            <span>地类</span>
            <small>选中面后点击可改类别</small>
          </div>
          <button
            v-for="definition in result.classes || []"
            :key="definition.class_code"
            class="class-option"
            :class="{ active: activeClassCode === definition.class_code }"
            type="button"
            @click="selectClass(definition.class_code)"
          >
            <span class="class-swatch" :style="{ backgroundColor: classColor(definition) }" />
            <span>{{ definition.class_name_zh || definition.class_name }}</span>
            <small>{{ definition.class_name }}</small>
          </button>
          <div class="panel-note">
            使用地图右上角的绘制按钮新建面；单击面后可拖动顶点、改类或删除。保存后会生成新的不可变版本。
          </div>
        </aside>

        <div class="map-card">
          <div ref="mapContainer" class="classification-map" />
          <div v-if="!hasBasemap" class="map-notice">
            当前项目没有可用底图，仍可编辑已有矢量；建议在 Miner 项目工作台先完成离线底图切片并启用。
          </div>
        </div>

        <aside class="editor-panel revision-panel">
          <div class="panel-heading">
            <span>版本记录</span>
            <small>仅查看，不支持回滚</small>
          </div>
          <el-empty v-if="!revisions.length" description="暂无版本记录" :image-size="72" />
          <ol v-else class="revision-list">
            <li v-for="revision in revisions" :key="revision.revision_no">
              <div>
                <strong>V{{ revision.revision_no }}</strong>
                <span>{{ revision.source === 'auto' ? '自动基线' : '人工编辑' }}</span>
              </div>
              <small>{{ revision.feature_count }} 个面 · {{ formatTime(revision.created_at) }}</small>
            </li>
          </ol>
        </aside>
      </div>
    </template>
  </section>
</template>

<script>
import { LngLatBounds, Map, NavigationControl } from 'maplibre-gl';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import 'maplibre-gl/dist/maplibre-gl.css';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';

import {
  exportClassificationResult,
  getClassificationResult,
  getClassificationRevisions,
  saveClassificationRevision,
} from '@/api/classificationResults';
import global from '@/global';
import {
  buildEditableFeatureCollection,
  isEditableVectorStatus,
  resolveSecureTileTemplate,
} from '@/utils/classificationEditor.mjs';
import { readClassificationResultContext } from '@/utils/classificationResultContext.mjs';


const FALLBACK_CENTER = [104.2, 25.1];
const FALLBACK_ZOOM = 5;
const CLASS_COLORS = {
  0: '#00ff00',
  1: '#008000',
  2: '#ff0000',
  3: '#ffff00',
  4: '#ff00ff',
  5: '#00bfff',
};

function emptyFeatureCollection() {
  return { type: 'FeatureCollection', features: [] };
}

function classColorExpression() {
  return [
    'match',
    ['get', 'user_class_code'],
    0, CLASS_COLORS[0],
    1, CLASS_COLORS[1],
    2, CLASS_COLORS[2],
    3, CLASS_COLORS[3],
    4, CLASS_COLORS[4],
    5, CLASS_COLORS[5],
    '#2bb6ad',
  ];
}

function drawStyles() {
  return [
    {
      id: 'gl-draw-polygon-fill',
      type: 'fill',
      filter: ['all', ['==', '$type', 'Polygon']],
      paint: {
        'fill-color': ['case', ['==', ['get', 'active'], 'true'], '#f59e0b', classColorExpression()],
        'fill-opacity': 0.32,
      },
    },
    {
      id: 'gl-draw-lines',
      type: 'line',
      filter: ['any', ['==', '$type', 'LineString'], ['==', '$type', 'Polygon']],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ['case', ['==', ['get', 'active'], 'true'], '#f59e0b', classColorExpression()],
        'line-dasharray': ['case', ['==', ['get', 'active'], 'true'], [0.2, 2], [2, 0]],
        'line-width': 2,
      },
    },
    {
      id: 'gl-draw-point-outer',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'feature']],
      paint: { 'circle-radius': 6, 'circle-color': '#ffffff' },
    },
    {
      id: 'gl-draw-point-inner',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'feature']],
      paint: { 'circle-radius': 4, 'circle-color': classColorExpression() },
    },
    {
      id: 'gl-draw-vertex-outer',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex'], ['!=', 'mode', 'simple_select']],
      paint: { 'circle-radius': 7, 'circle-color': '#ffffff' },
    },
    {
      id: 'gl-draw-vertex-inner',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex'], ['!=', 'mode', 'simple_select']],
      paint: { 'circle-radius': 5, 'circle-color': '#f59e0b' },
    },
    {
      id: 'gl-draw-midpoint',
      type: 'circle',
      filter: ['all', ['==', 'meta', 'midpoint']],
      paint: { 'circle-radius': 3, 'circle-color': '#f59e0b' },
    },
  ];
}

function validBounds(value) {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) return null;
  const [west, south, east, north] = value;
  if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) return null;
  return value;
}

function featureBounds(featureCollection) {
  const bounds = new LngLatBounds();
  let found = false;
  const visit = (value) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
      bounds.extend([value[0], value[1]]);
      found = true;
      return;
    }
    value.forEach(visit);
  };
  (featureCollection?.features || []).forEach((feature) => visit(feature?.geometry?.coordinates));
  return found ? bounds : null;
}

function createClientFeatureId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `client-${crypto.randomUUID()}`;
  }
  return `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function filenameFromContentDisposition(value) {
  const match = /filename="?([^";]+)"?/i.exec(String(value || ''));
  return match ? match[1] : 'classification-result.geojson';
}

export default {
  name: 'ClassificationResultEditor',
  data() {
    return {
      context: null,
      result: null,
      revisions: [],
      loading: true,
      loadError: '',
      saving: false,
      exporting: false,
      isDirty: false,
      activeClassCode: 0,
      selectedDrawIds: [],
      map: null,
      draw: null,
      tileTemplate: null,
    };
  },
  computed: {
    hasBasemap() {
      return Boolean(this.tileTemplate);
    },
  },
  watch: {
    '$route.fullPath'() {
      this.reloadResult();
    },
  },
  mounted() {
    this.reloadResult();
  },
  beforeUnmount() {
    this.destroyMap();
    // 组件销毁后丢弃在途响应（route query 变化会复用实例并发起第二次 reload）
    this.reloadSeq = -1;
  },
  methods: {
    async reloadResult() {
      // 竞态守卫：两次 reload 并发时（前进/后退、重复进入），先发后至的旧响应
      // 会把 A 成果的数据装进 B 成果的 context，保存时即写错对象
      const seq = (this.reloadSeq = (this.reloadSeq || 0) + 1);
      this.destroyMap();
      this.context = readClassificationResultContext(window.location.search);
      this.result = null;
      this.revisions = [];
      this.loadError = '';
      this.isDirty = false;
      this.selectedDrawIds = [];
      this.loading = true;
      if (!this.context) {
        this.loading = false;
        this.loadError = '缺少有效的 project_id 或 result_id，无法打开分类成果。';
        return;
      }
      try {
        const [result, revisionPayload] = await Promise.all([
          getClassificationResult(this.context.projectId, this.context.resultId),
          getClassificationRevisions(this.context.projectId, this.context.resultId),
        ]);
        if (seq !== this.reloadSeq || seq === -1) {
          // 过期响应（已被更新的 reload 或卸载取代）：丢弃，不覆盖新状态
          return;
        }
        this.result = result;
        this.revisions = revisionPayload?.revisions || [];
        this.activeClassCode = Number(result?.classes?.[0]?.class_code ?? 0);
        this.tileTemplate = resolveSecureTileTemplate(result?.map_manifest?.api_tile_url, global.BASEURL);
        await this.$nextTick();
        if (isEditableVectorStatus(result.vector_status)) {
          this.initializeMap();
        }
      } catch (error) {
        if (seq !== this.reloadSeq) {
          return;
        }
        this.loadError = error?.message || '分类成果读取失败';
      } finally {
        if (seq === this.reloadSeq) {
          this.loading = false;
        }
      }
    },
    initializeMap() {
      if (!this.$refs.mapContainer || !this.result || this.map) return;
      const mapManifest = this.result.map_manifest || {};
      const bounds = validBounds(mapManifest.bounds);
      const center = bounds
        ? [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
        : FALLBACK_CENTER;
      const sources = {};
      const layers = [{ id: 'editor-background', type: 'background', paint: { 'background-color': '#edf3f1' } }];
      if (this.tileTemplate) {
        sources.project_basemap = { type: 'raster', tiles: [this.tileTemplate], tileSize: 256 };
        layers.push({ id: 'project-basemap', type: 'raster', source: 'project_basemap', paint: { 'raster-opacity': 0.92 } });
      }
      const backendOrigin = new URL(global.BASEURL).origin;
      this.map = new Map({
        container: this.$refs.mapContainer,
        style: { version: 8, sources, layers },
        center,
        zoom: bounds ? 10 : FALLBACK_ZOOM,
        transformRequest: (url) => ({
          url,
          ...(url.startsWith(backendOrigin) ? { credentials: 'include' } : {}),
        }),
      });
      this.map.on('load', () => {
        if (!this.map) return;
        this.map.addControl(new NavigationControl(), 'top-left');
        this.draw = new MapboxDraw({
          displayControlsDefault: false,
          controls: { polygon: true, trash: true },
          userProperties: true,
          styles: drawStyles(),
        });
        this.map.addControl(this.draw, 'top-right');
        this.resetDrawing();
        this.map.on('draw.create', this.handleDrawCreate);
        this.map.on('draw.update', this.handleDrawUpdate);
        this.map.on('draw.delete', this.handleDrawDelete);
        this.map.on('draw.selectionchange', this.handleSelectionChange);
        this.fitToCurrentFeatures(bounds);
      });
    },
    destroyMap() {
      if (this.map) {
        this.map.remove();
      }
      this.map = null;
      this.draw = null;
      this.tileTemplate = null;
    },
    fitToCurrentFeatures(manifestBounds = null) {
      if (!this.map) return;
      const featureCollection = this.result?.current_feature_collection || emptyFeatureCollection();
      const currentBounds = featureBounds(featureCollection);
      if (currentBounds) {
        this.map.fitBounds(currentBounds, { padding: 64, maxZoom: 16, duration: 0 });
        return;
      }
      if (manifestBounds) {
        this.map.fitBounds([[manifestBounds[0], manifestBounds[1]], [manifestBounds[2], manifestBounds[3]]], {
          padding: 64,
          maxZoom: 15,
          duration: 0,
        });
      }
    },
    resetDrawing() {
      if (!this.draw || !this.result) return;
      this.draw.set(this.result.current_feature_collection || emptyFeatureCollection());
      this.isDirty = false;
      this.selectedDrawIds = [];
    },
    selectClass(classCode) {
      this.activeClassCode = classCode;
      if (!this.draw || !this.selectedDrawIds.length) return;
      this.selectedDrawIds.forEach((drawId) => {
        this.draw.setFeatureProperty(drawId, 'class_code', classCode);
      });
      this.isDirty = true;
    },
    handleDrawCreate(event) {
      event.features.forEach((feature) => {
        this.draw.setFeatureProperty(feature.id, 'class_code', this.activeClassCode);
        this.draw.setFeatureProperty(feature.id, 'feature_id', createClientFeatureId());
      });
      this.isDirty = true;
    },
    handleDrawUpdate() {
      this.isDirty = true;
    },
    handleDrawDelete() {
      this.isDirty = true;
      this.selectedDrawIds = [];
    },
    handleSelectionChange(event) {
      this.selectedDrawIds = (event.features || []).map((feature) => feature.id);
    },
    async saveDrawing() {
      if (!this.context || !this.result || !this.draw) return;
      let featureCollection;
      try {
        featureCollection = buildEditableFeatureCollection(this.draw.getAll());
      } catch (error) {
        this.$message.error(error?.message || '待保存的矢量数据无效');
        return;
      }
      this.saving = true;
      try {
        const saved = await saveClassificationRevision(this.context.projectId, this.context.resultId, {
          base_revision_no: this.result.current_revision_no,
          feature_collection: featureCollection,
        });
        this.result.current_feature_collection = saved.feature_collection;
        this.result.current_revision_no = saved.current_revision_no;
        this.draw.set(saved.feature_collection || emptyFeatureCollection());
        this.isDirty = false;
        this.selectedDrawIds = [];
        await this.loadRevisions();
        this.$message.success(`已保存为 V${saved.current_revision_no}`);
      } catch (error) {
        if (error?.status === 409) {
          this.$message.error('该成果已有新版本，请重新加载后再编辑；系统不会自动覆盖他人的修改。');
        } else if (error?.status === 422) {
          const reason = error?.details?.reason || error?.details?.message || error?.message;
          this.$message.error(`无法保存：${reason}`);
        } else {
          this.$message.error(error?.message || '保存分类成果失败');
        }
      } finally {
        this.saving = false;
      }
    },
    async loadRevisions() {
      if (!this.context) return;
      const payload = await getClassificationRevisions(this.context.projectId, this.context.resultId);
      this.revisions = payload?.revisions || [];
    },
    async downloadCurrent() {
      if (!this.context) return;
      this.exporting = true;
      try {
        const response = await exportClassificationResult(this.context.projectId, this.context.resultId);
        const objectUrl = URL.createObjectURL(response.data);
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = filenameFromContentDisposition(response.headers?.['content-disposition']);
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
      } catch (error) {
        this.$message.error(error?.message || '导出当前分类成果失败');
      } finally {
        this.exporting = false;
      }
    },
    classColor(definition) {
      const rgb = definition?.rgb;
      if (Array.isArray(rgb) && rgb.length === 3) return `rgb(${rgb.join(',')})`;
      return CLASS_COLORS[definition?.class_code] || '#2bb6ad';
    },
    formatTime(value) {
      if (!value) return '时间未知';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
    },
    goBack() {
      const query = this.context ? { project_id: String(this.context.projectId) } : {};
      this.$router.push({ path: '/segmentation', query });
    },
  },
};
</script>

<style scoped>
.classification-editor {
  min-height: calc(100vh - 84px);
  padding: 24px 0 38px;
  color: var(--text-primary);
}

.editor-header,
.editor-toolbar,
.editor-workspace,
.editor-alert {
  max-width: 1480px;
  margin-right: auto;
  margin-left: auto;
}

.editor-header,
.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.editor-header {
  margin-bottom: 18px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--primary-color);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0;
  font-size: clamp(24px, 3vw, 34px);
  line-height: 1.18;
}

.subtitle {
  margin: 8px 0 0;
  color: var(--text-secondary);
}

.header-actions,
.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}

.editor-alert {
  margin-top: 22px;
}

.editor-toolbar {
  margin-top: 22px;
  margin-bottom: 14px;
  min-height: 64px;
  padding: 0 16px;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--bg-card);
  box-shadow: var(--shadow-sm);
}

.toolbar-state {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--text-secondary);
  font-size: 14px;
}

.state-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--success-color);
}

.state-dot.dirty {
  background: #f59e0b;
}

.selection-count {
  padding-left: 9px;
  border-left: 1px solid var(--border-color);
  color: var(--text-muted);
}

.editor-workspace {
  display: grid;
  grid-template-columns: 220px minmax(480px, 1fr) 260px;
  gap: 14px;
  min-height: 650px;
}

.editor-panel,
.map-card {
  overflow: hidden;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--bg-card);
  box-shadow: var(--shadow-sm);
}

.editor-panel {
  padding: 15px;
}

.panel-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--border-light);
}

.panel-heading span {
  font-weight: 700;
}

.panel-heading small,
.panel-note,
.revision-list small {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.55;
}

.class-option {
  display: grid;
  grid-template-columns: 14px 1fr;
  align-items: center;
  width: 100%;
  margin-top: 9px;
  padding: 9px 8px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
}

.class-option:hover,
.class-option.active {
  border-color: rgba(43, 182, 173, 0.38);
  background: var(--primary-hover);
}

.class-option small {
  grid-column: 2;
  margin-top: 2px;
  color: var(--text-muted);
}

.class-swatch {
  width: 11px;
  height: 11px;
  border: 1px solid rgba(31, 45, 61, 0.28);
  border-radius: 50%;
}

.panel-note {
  margin-top: 17px;
  padding-top: 14px;
  border-top: 1px solid var(--border-light);
}

.map-card {
  position: relative;
  min-height: 650px;
}

.classification-map {
  width: 100%;
  min-height: 650px;
}

.map-notice {
  position: absolute;
  right: 14px;
  bottom: 14px;
  max-width: 330px;
  padding: 10px 12px;
  border: 1px solid rgba(217, 119, 6, 0.24);
  border-radius: 9px;
  background: rgba(255, 251, 235, 0.94);
  color: #92400e;
  font-size: 12px;
  line-height: 1.55;
}

.revision-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.revision-list li {
  padding: 13px 0;
  border-bottom: 1px solid var(--border-light);
}

.revision-list li:last-child {
  border-bottom: 0;
}

.revision-list div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.revision-list span {
  color: var(--text-secondary);
  font-size: 12px;
}

@media (max-width: 1120px) {
  .editor-workspace {
    grid-template-columns: 200px minmax(420px, 1fr);
  }

  .revision-panel {
    grid-column: 1 / -1;
  }

  .revision-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0 16px;
  }
}

@media (max-width: 760px) {
  .classification-editor {
    padding-top: 14px;
  }

  .editor-header,
  .editor-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-actions {
    justify-content: flex-start;
  }

  .editor-workspace {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .class-panel {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0 8px;
  }

  .class-panel .panel-heading,
  .class-panel .panel-note {
    grid-column: 1 / -1;
  }

  .map-card,
  .classification-map {
    min-height: 500px;
  }
}
</style>
