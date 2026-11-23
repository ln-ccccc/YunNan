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
        <el-button :disabled="saving" plain @click="goBack">返回分类结果</el-button>
        <el-button :loading="loading" :disabled="saving" plain @click="reloadResult">重新加载</el-button>
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
      <!-- 底图覆盖提示独立于 vector_failed 的 v-if/v-else 链：必须放在其前，
           否则工具栏的 v-else 会改绑到本告警，有提示时隐藏整个编辑工具栏 -->
      <el-alert
        v-if="coverageNotice"
        class="editor-alert"
        :type="coverageNotice.level"
        :closable="false"
        :title="coverageNotice.message"
        show-icon
      />

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
          <el-button :disabled="!canUndo || saving" plain title="Ctrl+Z" @click="undoLastEdit">撤销</el-button>
          <el-button :disabled="!canRedo || saving" plain title="Ctrl+Y" @click="redoLastEdit">重做</el-button>
          <el-button :disabled="!isDirty || saving" plain @click="discardAllEdits">放弃全部修改</el-button>
          <el-button
            :disabled="selectedDrawIds.length < 2 || mixedClassSelection || saving"
            :title="mixedClassSelection ? '仅支持合并同地类的面' : '合并同地类的相邻选中面'"
            plain
            @click="mergeSelectedFeatures"
          >
            合并选中
          </el-button>
          <el-button :disabled="!selectedDrawIds.length || saving" plain title="轻度简化选中面边缘（≈1m 容差）" @click="simplifySelectedFeatures">消除锯齿</el-button>
          <el-button
            :loading="exporting"
            :title="isDirty ? '导出当前画布（含未保存修改）的 GeoJSON 草稿' : '导出已保存版本的 GeoJSON'"
            plain
            @click="downloadCurrent"
          >
            {{ isDirty ? '导出草稿 GeoJSON' : '导出已保存 GeoJSON' }}
          </el-button>
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
            :disabled="saving"
            @click="selectClass(definition.class_code)"
          >
            <span class="class-swatch" :style="{ backgroundColor: classColor(definition) }" />
            <span>{{ definition.class_name_zh || definition.class_name }}</span>
            <small>{{ definition.class_name }}</small>
          </button>
          <div class="panel-note">
            使用地图右上角的绘制按钮新建面；单击选中后可拖动顶点、改类或删除，Shift+点击可多选
            （多选后改类即批量改类，两个及以上同地类面可合并）。保存后会生成新的不可变版本。
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
          <!-- 版本列表不阻塞首屏（2026-11-23 P1）：加载中不得显示"暂无版本记录"误导用户 -->
          <div v-if="revisionsLoading" class="revision-loading">版本记录加载中…</div>
          <el-empty v-else-if="!revisions.length" description="暂无版本记录" :image-size="72" />
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
import { Map, NavigationControl } from 'maplibre-gl';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import union from '@turf/union';
import simplify from '@turf/simplify';
import booleanWithin from '@turf/boolean-within';
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
  basemapCoverageNotice,
  buildAssignedFeatureIdMap,
  buildEditableFeatureCollection,
  createGuardedDirectSelect,
  describeVertexDeletionBlockers,
  draftExportFilename,
  isEditableVectorStatus,
  resolveFocusBounds,
  resolveSecureTileTemplate,
  resolveTileZoomRange,
  rewriteFeatureCollectionJson,
} from '@/utils/classificationEditor.mjs';
import { polygonStructureProblem, simplifyPolygonSafely } from '@/utils/classificationEditorGeometry.mjs';
import { createEditorHistory } from '@/utils/classificationEditorHistory.mjs';
import {
  classColor as resolveClassColor,
  classColorExpression,
} from '@/utils/classificationColors.mjs';
import { readClassificationResultContext } from '@/utils/classificationResultContext.mjs';


const FALLBACK_CENTER = [104.2, 25.1];
const FALLBACK_ZOOM = 5;

function emptyFeatureCollection() {
  return { type: 'FeatureCollection', features: [] };
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
        // mapbox-gl-draw 的 active 是字符串 'true'/'false'（constants.activeStates），
        // 官方 theme 即 ['==',['get','active'],'true']；dasharray 数组字面量必须
        // literal 包裹（裸数组被 style-spec 当表达式解析，加层抛错中断渲染）
        'line-dasharray': ['case', ['==', ['get', 'active'], 'true'], ['literal', [0.2, 2]], ['literal', [2, 0]]],
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

function triggerDownload(objectUrl, filename) {
  /* 服务端导出与草稿导出共用的浏览器下载触发（Object URL 用后即释放） */
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
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
      // 版本列表加载中（不阻塞首屏，A2）：为真时版本面板显示"版本记录加载中…"
      revisionsLoading: false,
      loading: true,
      loadError: '',
      // 底图覆盖提示（2026-11-23 处置 B）：null=不提示
      coverageNotice: null,
      saving: false,
      exporting: false,
      isDirty: false,
      activeClassCode: 0,
      selectedDrawIds: [],
      // 选中面的地类集合（合并按钮置灰依据，E2-b）：与 selectedDrawIds 同步维护，
      // 只经 setSelectedDrawIds 写入
      selectedClassCodes: [],
      // 撤销/重做历史（纯逻辑在 utils/classificationEditorHistory.mjs），
      // canUndo/canRedo 以 data 字段同步供模板响应
      history: null,
      canUndo: false,
      canRedo: false,
      suppressDrawEvents: false,
      // 编辑代次：所有经 recordMutation 的修改递增。保存响应据此识别"保存
      // 期间又发生的新编辑"（配合提交快照比对兜底未结束手势，见 saveDrawing）
      editGeneration: 0,
      map: null,
      draw: null,
      tileTemplate: null,
    };
  },
  computed: {
    hasBasemap() {
      return Boolean(this.tileTemplate);
    },
    // 选中面地类不一致（E2-b）：合并按钮置灰并用 title 说明，
    // 不再等用户点了才报"仅支持合并同地类的面"
    mixedClassSelection() {
      return this.selectedClassCodes.length > 1;
    },
  },
  watch: {
    '$route.fullPath'() {
      this.reloadResult();
    },
  },
  mounted() {
    document.addEventListener('keydown', this.handleKeydown);
    window.addEventListener('beforeunload', this.handleBeforeUnload);
    this.reloadResult();
  },
  beforeUnmount() {
    document.removeEventListener('keydown', this.handleKeydown);
    window.removeEventListener('beforeunload', this.handleBeforeUnload);
    this.destroyMap();
    // 组件销毁后丢弃在途响应（route query 变化会复用实例并发起第二次 reload）
    this.reloadSeq = -1;
  },
  // 审校几十个图斑的误触保护：isDirty 时离开路由必须显式确认（P1-7，
  // geoview-glass-smoke 在案 knownBug 的反转目标）
  async beforeRouteLeave(to, from, next) {
    if (!(await this.confirmDiscardUnsaved())) {
      next(false);
      return;
    }
    next();
  },
  methods: {
    // 三处未保存丢弃防线共用（P1-7）：重新加载 / 路由切换 / 浏览器关闭或刷新
    confirmDiscardUnsaved() {
      if (!this.isDirty) return Promise.resolve(true);
      return this.$confirm(
        '当前存在未保存的编辑，继续将丢弃这些修改（撤销栈也会一并清空）。',
        '未保存提醒',
        { confirmButtonText: '丢弃修改并继续', cancelButtonText: '继续编辑', type: 'warning' },
      )
        .then(() => true)
        .catch(() => false);
    },
    handleBeforeUnload(event) {
      if (!this.isDirty) return;
      // 浏览器约定：preventDefault + returnValue 才会弹原生离开确认
      event.preventDefault();
      event.returnValue = '';
    },
    async reloadResult() {
      if (!(await this.confirmDiscardUnsaved())) return;
      // 竞态守卫：两次 reload 并发时（前进/后退、重复进入），先发后至的旧响应
      // 会把 A 成果的数据装进 B 成果的 context，保存时即写错对象
      const seq = (this.reloadSeq = (this.reloadSeq || 0) + 1);
      this.destroyMap();
      this.context = readClassificationResultContext(window.location.search);
      this.result = null;
      this.revisions = [];
      this.revisionsLoading = false;
      this.loadError = '';
      this.isDirty = false;
      this.setSelectedDrawIds([]);
      this.loading = true;
      // 切换成果时复位：在途旧保存/导出的 finally 不再清理新上下文的标志
      this.saving = false;
      this.exporting = false;
      if (!this.context) {
        this.loading = false;
        this.loadError = '缺少有效的 project_id 或 result_id，无法打开分类成果。';
        return;
      }
      try {
        // 首屏只等成果详情（2026-11-23 P1）：详情才是画布的门控数据。此前
        // Promise.all 把详情与版本列表绑死，版本接口慢时骨架屏一直挂着
        const result = await getClassificationResult(this.context.projectId, this.context.resultId);
        if (seq !== this.reloadSeq || seq === -1) {
          // 过期响应（已被更新的 reload 或卸载取代）：丢弃，不覆盖新状态
          return;
        }
        this.result = result;
        this.activeClassCode = Number(result?.classes?.[0]?.class_code ?? 0);
        this.tileTemplate = resolveSecureTileTemplate(result?.map_manifest?.api_tile_url, global.BASEURL);
        // 关注范围（E1）：图斑 → ROI → manifest。ready_empty 成果没有图斑时按
        // ROI 判定覆盖，不再因"无图斑"直接不提示
        const manifestBounds = validBounds(result?.map_manifest?.bounds);
        this.coverageNotice = basemapCoverageNotice(
          resolveFocusBounds(result?.current_feature_collection, result?.roi_geometry, manifestBounds),
          manifestBounds,
        );
        // 版本列表 best-effort：不阻塞画布，失败单独提示。提示要过会话归属——
        // A 成果的失败提示不得弹到已切换的 B 成果上
        this.loadRevisions().catch(() => {
          if (seq !== this.reloadSeq || seq === -1) return;
          this.$message.warning('版本列表读取失败，可点击"重新加载"重试；不影响画布编辑。');
        });
      } catch (error) {
        if (seq !== this.reloadSeq) {
          return;
        }
        this.loadError = error?.message || '分类成果读取失败';
      } finally {
        if (seq === this.reloadSeq) {
          this.loading = false;
          // 地图初始化必须在 loading 落为 false 之后：编辑器主体（含地图容器）
          // 被 v-else-if="loading" 的骨架屏门控，loading=true 期间 $refs.mapContainer
          // 不存在，此前在 try 内 nextTick 时机初始化会静默早退——地图永不渲染
          // （2026-09-20 GUI 实测发现的 P1）
          await this.$nextTick();
          if (
            this.result &&
            !this.loadError &&
            isEditableVectorStatus(this.result.vector_status)
          ) {
            this.initializeMap();
          }
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
        // 底图金字塔声明范围应用到 raster source（2026-10-31 审计 P2）：放大
        // 超过原生最大级别时 MapLibre 对最后可用级别过缩放渲染，而不是继续
        // 请求不存在的瓦片；不设 Map maxZoom，允许在有效源级别上继续放大
        sources.project_basemap = {
          type: 'raster',
          tiles: [this.tileTemplate],
          tileSize: 256,
          ...resolveTileZoomRange(mapManifest),
        };
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
        // 顶点删除防护挂在真实入口（2026-11-23 P1）：删除按钮与 Delete/Backspace
        // 键盘两条路都汇到 direct_select.onTrash。此前只在地图容器 click 捕获阶段
        // 拦截：键盘完全绕过；且只判"面当前是否 ≤3 顶点"，四边形 Shift 选两个
        // 顶点再删除会整面被删；对 MultiPolygon 把环数当顶点数，合法删除被误拦
        this.draw = new MapboxDraw({
          displayControlsDefault: false,
          controls: { polygon: true, trash: true },
          userProperties: true,
          styles: drawStyles(),
          modes: {
            ...MapboxDraw.modes,
            direct_select: createGuardedDirectSelect(
              MapboxDraw.modes.direct_select,
              (blockers, geometry) => {
                // 选项必须放在第一个对象参数里：Element Plus 的 $message.warning(text, X)
                // 第二个参数是应用上下文(appContext)，传 { grouping } 会被当成上下文，
                // 消息组件渲染时抛 "Object prototype may only be an Object or null"——
                // 面被保住了，提示却弹不出来，看起来像删除按钮坏了
                // （2026-11-24 真实浏览器验收抓到；Node 单测用假处理器碰不到真实消息组件）。
                // grouping：连按 Delete 时同文案合并，不堆叠
                this.$message.warning({
                  message: describeVertexDeletionBlockers(blockers, geometry),
                  grouping: true,
                });
              },
            ),
          },
        });
        this.map.addControl(this.draw, 'top-right');
        // direct_select 下未点选顶点时删除按钮是无操作（mapbox-gl-draw 1.5 行为，
        // 2026-11 验收遗留）：点击无效果且无提示，用户以为删除坏了。捕获点击给引导。
        // 注意必须挂容器捕获阶段：draw 控件自身的按钮 click 监听会拦截同元素上
        // 后注册的监听（浏览器实测 click 探针不触发），按钮上 addEventListener 是死代码。
        // 顶点删除防护已移到 direct_select.onTrash（见上），这里只负责引导提示
        this.map.getContainer().addEventListener(
          'click',
          (e) => {
            if (!(e.target instanceof Element) || !e.target.closest('.mapbox-gl-draw_trash')) return;
            if (!this.draw || this.draw.getMode() !== 'direct_select') return;
            const selectedPoints = this.draw.getSelectedPoints?.();
            const hasSelectedVertex = !!(selectedPoints && selectedPoints.features && selectedPoints.features.length);
            if (!hasSelectedVertex) {
              this.$message.info(
                '当前处于顶点编辑模式：请先点击选中某个顶点，删除按钮才会移除该顶点；要删除整个面，请按 Esc 退出顶点编辑后单击选中面再删除',
              );
            }
          },
          true,
        );
        // Esc 退出 direct_select（2026-11-23 P1）：draw 1.5.1 顶点模式不处理
        // Escape，上面引导文案里的"按 Esc 退出"此前无法执行；容器 keydown 补齐
        this.map.getContainer().addEventListener(
          'keydown',
          (e) => {
            if (e.key !== 'Escape' || !this.draw || this.draw.getMode() !== 'direct_select') return;
            const selectedId = this.draw.getSelected?.()?.features?.[0]?.id;
            this.draw.changeMode('simple_select', selectedId != null ? { featureIds: [selectedId] } : {});
          },
        );
        // ROI 边界叠加（2026-11-13 风险复盘）：此前绘制越界只能等保存 422 才
        // 暴露，现把矿山边界画在画布上并在绘制/拖顶点时即时提示
        const roiGeometry = this.result?.roi_geometry;
        this.map.addSource('result-roi', {
          type: 'geojson',
          data: roiGeometry
            ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: roiGeometry }] }
            : emptyFeatureCollection(),
        });
        this.map.addLayer({
          id: 'result-roi-outline',
          type: 'line',
          source: 'result-roi',
          paint: { 'line-color': '#f43f5e', 'line-width': 1.5, 'line-dasharray': [3, 2] },
        });
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
      // 定位优先级与底图覆盖提示共用 resolveFocusBounds（E1）：图斑范围 → ROI 范围 →
      // manifest 范围。ready_empty 成果没有图斑时按 ROI（矿山边界）定位，
      // 不再回退到整张底图范围（看到整个云南，找不到矿山在哪）
      const contentBounds = resolveFocusBounds(featureCollection, this.result?.roi_geometry, null);
      const focus = contentBounds || (validBounds(manifestBounds) ? manifestBounds : null);
      if (!focus) return;
      // 图斑与 ROI 是"要看的内容"，允许放大到 16 级；manifest 只是底图范围，保持原参数
      this.map.fitBounds(
        [[focus[0], focus[1]], [focus[2], focus[3]]],
        contentBounds ? { padding: 64, maxZoom: 16, duration: 0 } : { padding: 64, maxZoom: 15, duration: 0 },
      );
    },
    resetDrawing() {
      if (!this.draw || !this.result) return;
      this.draw.set(this.result.current_feature_collection || emptyFeatureCollection());
      this.resetEditHistory();
      this.setSelectedDrawIds([]);
    },
    setSelectedDrawIds(drawIds) {
      /* selectedDrawIds 的唯一写入口：同步刷新选中面的地类集合（合并按钮置灰依据） */
      this.selectedDrawIds = drawIds;
      this.syncSelectionClassCodes();
    },
    syncSelectionClassCodes() {
      // draw.get 对已不存在的要素会抛错，逐个安全读取
      const codes = new Set();
      this.selectedDrawIds.forEach((drawId) => {
        let feature = null;
        try {
          feature = this.draw?.get(drawId) || null;
        } catch (_) {
          feature = null;
        }
        const classCode = feature?.properties?.class_code;
        if (classCode !== undefined && classCode !== null) codes.add(classCode);
      });
      this.selectedClassCodes = [...codes];
    },
    // 「放弃全部修改」二次确认（E2-a，2026-11-23）：此前点按钮直接 resetDrawing()，
    // 无提示丢弃全部编辑。脏检查与按钮禁用条件不变，取消则不动。
    async discardAllEdits() {
      if (!this.isDirty || !this.draw) return;
      const seq = this.reloadSeq;
      const confirmed = await this.$confirm(
        '将丢弃当前全部未保存的编辑（撤销栈也会一并清空），此操作无法恢复。',
        '放弃修改',
        { confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning' },
      )
        .then(() => true)
        .catch(() => false);
      if (!confirmed) return;
      // 确认框打开期间可能已切换成果、或已不再有未保存修改：不得重置新上下文
      if (seq !== this.reloadSeq || !this.isDirty) return;
      this.resetDrawing();
    },
    currentDrawJson() {
      if (!this.draw) return '';
      return JSON.stringify(this.draw.getAll());
    },
    resetEditHistory() {
      this.history = createEditorHistory();
      this.history.reset(this.currentDrawJson());
      this.syncHistoryState();
    },
    syncHistoryState() {
      this.canUndo = Boolean(this.history?.canUndo);
      this.canRedo = Boolean(this.history?.canRedo);
      this.isDirty = Boolean(this.history?.isDirty(this.currentDrawJson()));
    },
    recordMutation(kind = 'edit') {
      if (!this.history) return;
      this.editGeneration += 1;
      this.history.record(kind, this.currentDrawJson(), Date.now());
      this.syncHistoryState();
    },
    undoLastEdit() {
      if (!this.draw || !this.history?.canUndo) return;
      const previous = this.history.undo(this.currentDrawJson());
      if (!previous) return;
      this.draw.set(JSON.parse(previous));
      this.setSelectedDrawIds([]);
      this.syncHistoryState();
    },
    redoLastEdit() {
      if (!this.draw || !this.history?.canRedo) return;
      const next = this.history.redo(this.currentDrawJson());
      if (!next) return;
      this.draw.set(JSON.parse(next));
      this.setSelectedDrawIds([]);
      this.syncHistoryState();
    },
    handleKeydown(event) {
      if (!this.draw || this.saving) return;
      const target = event.target;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
      const key = String(event.key || '').toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === 'z' && !event.shiftKey) {
        event.preventDefault();
        this.undoLastEdit();
      } else if ((event.ctrlKey || event.metaKey) && (key === 'y' || (key === 'z' && event.shiftKey))) {
        event.preventDefault();
        this.redoLastEdit();
      }
    },
    selectClass(classCode) {
      // 保存在途时冻结改类入口：请求体与响应数据对应的是发出瞬间的画布，
      // 此时改类会被响应覆盖（2026-10-31 审计 P1）；绘制类手势无法干净冻结，
      // 由 saveDrawing 的代次+提交快照双比对兜底保留
      if (this.saving) return;
      this.activeClassCode = classCode;
      if (!this.draw || !this.selectedDrawIds.length) return;
      this.selectedDrawIds.forEach((drawId) => {
        this.draw.setFeatureProperty(drawId, 'class_code', classCode);
      });
      // 批量改类后选中面地类必然一致：同步刷新，解除合并按钮的"异类置灰"
      this.syncSelectionClassCodes();
      this.recordMutation('properties');
    },
    handleDrawCreate(event) {
      // 防御位：mapbox-gl-draw 1.5.1 默认 suppressAPIEvents:true，编程式 set/add/delete
      // 本就不触发事件；此标志为未来升级关闭该默认时的防护
      if (this.suppressDrawEvents) return;
      event.features.forEach((feature) => {
        this.draw.setFeatureProperty(feature.id, 'class_code', this.activeClassCode);
        this.draw.setFeatureProperty(feature.id, 'feature_id', createClientFeatureId());
      });
      this.recordMutation('create');
      this.warnFeaturesOutsideRoi(event.features);
    },
    handleDrawUpdate(event) {
      // 事件参数必须接收（2026-11-23 修复）：此前缺参，body 引用 event 抛
      // ReferenceError，顶点拖动的 ROI 即时警告从未生效（P2-1）
      if (this.suppressDrawEvents) return;
      this.recordMutation('vertex');
      this.warnFeaturesOutsideRoi(event?.features, '，请调整顶点位置');
    },
    roiContainsGeometry(geometry) {
      const roi = this.result?.roi_geometry;
      if (!roi || !geometry) return true;
      try {
        return booleanWithin(geometry, roi);
      } catch (_) {
        // 提示判定异常时不拦截绘制；保存时后端校验仍是唯一防线
        return true;
      }
    },
    warnFeaturesOutsideRoi(features, actionHint) {
      if (!Array.isArray(features) || !features.length) return;
      const outside = features.filter((feature) => !this.roiContainsGeometry(feature.geometry));
      if (!outside.length) return;
      this.$message.warning(
        `${outside.length} 个面超出成果 ROI（矿山边界），保存时将被拒绝${actionHint || '，请拖回界内或删除'}`,
      );
    },
    handleDrawDelete() {
      if (this.suppressDrawEvents) return;
      this.setSelectedDrawIds([]);
      this.recordMutation('delete');
    },
    handleSelectionChange(event) {
      this.setSelectedDrawIds((event.features || []).map((feature) => feature.id));
    },
    mergeSelectedFeatures() {
      if (!this.draw || this.selectedDrawIds.length < 2) return;
      const selected = this.selectedDrawIds
        .map((id) => {
          try {
            return this.draw.get(id);
          } catch (_) {
            return null;
          }
        })
        .filter((feature) => feature && ['Polygon', 'MultiPolygon'].includes(feature.geometry?.type));
      if (selected.length < 2) {
        this.$message.warning('选中的可合并面不足两个');
        return;
      }
      const classCodes = new Set(selected.map((feature) => feature.properties?.class_code));
      if (classCodes.size > 1) {
        this.$message.warning('仅支持合并同地类的面；不同地类请分别合并');
        return;
      }
      try {
        const merged = union({
          type: 'FeatureCollection',
          features: selected.map((feature) => ({ type: 'Feature', properties: {}, geometry: feature.geometry })),
        });
        const geometry = merged?.type === 'Feature' ? merged.geometry : merged?.features?.[0]?.geometry;
        if (!geometry) throw new Error('union 返回空几何');
        // 结构校验（E2-b）：@turf/union 7.4 返回 Polygon / MultiPolygon；
        // 类型不符、没有子面或外环不足 4 个坐标时沿用下方失败提示
        const structureProblem = polygonStructureProblem(geometry);
        if (structureProblem) throw new Error(`union 返回无效几何：${structureProblem}`);
        const classCode = selected[0].properties?.class_code ?? this.activeClassCode;
        this.suppressDrawEvents = true;
        try {
          // 先 add 后 delete：add 失败（无效几何）时原图不被破坏，catch 提示与实际一致
          this.draw.add({
            type: 'Feature',
            id: createClientFeatureId(),
            properties: { class_code: classCode, feature_id: createClientFeatureId() },
            geometry,
          });
          this.draw.delete(selected.map((feature) => feature.id));
        } finally {
          this.suppressDrawEvents = false;
        }
        this.setSelectedDrawIds([]);
        this.recordMutation('merge');
        this.$message.success(`已合并为 1 个面（原 ${selected.length} 个）`);
      } catch (_) {
        this.$message.error('合并失败：几何无效或边界过于复杂，可手动调整后重试');
      }
    },
    simplifySelectedFeatures() {
      if (!this.draw || !this.selectedDrawIds.length) return;
      // 容差 ≈1.1m（0.00001°）：消除栅格掩膜边缘锯齿而不明显改变地物形状
      const tolerance = 0.00001;
      let simplifiedCount = 0;
      let skippedCount = 0;
      const ids = [...this.selectedDrawIds];
      this.suppressDrawEvents = true;
      try {
        ids.forEach((id) => {
          try {
            const feature = this.draw.get(id);
            if (!feature || !['Polygon', 'MultiPolygon'].includes(feature.geometry?.type)) return;
            // 简化 → 校验 → 不通过则容差 /4、/16 重试（E2-c）：@turf/simplify 是不保
            // 拓扑的 Douglas-Peucker，可能产出自相交 / 退化环，此前要到保存时才被
            // 后端判无效返回 422。仍不通过则跳过，原面保持不变
            const outcome = simplifyPolygonSafely(feature.geometry, {
              tolerance,
              simplify: (geometry, currentTolerance) => simplify(
                { type: 'Feature', properties: {}, geometry },
                { tolerance: currentTolerance, highQuality: false },
              ).geometry,
            });
            if (!outcome) {
              skippedCount += 1;
              return;
            }
            // turf 输出 geometry 键序与 draw.get 不同，整对象 stringify 恒不等——按坐标比较
            if (
              JSON.stringify(outcome.geometry.coordinates) ===
              JSON.stringify(feature.geometry.coordinates)
            ) return;
            // 先 add 后 delete（同 merge 原子化策略）；单面退化（环点数不足等）跳过不中断整批
            this.draw.add({
              type: 'Feature',
              id: createClientFeatureId(),
              properties: feature.properties,
              geometry: outcome.geometry,
            });
            this.draw.delete(id);
            simplifiedCount += 1;
          } catch (_) {
            skippedCount += 1;
          }
        });
      } finally {
        this.suppressDrawEvents = false;
      }
      if (skippedCount) {
        this.$message.warning(`${skippedCount} 个面简化后会产生无效几何，已跳过，原面保持不变`);
      }
      if (simplifiedCount) {
        this.setSelectedDrawIds([]);
        this.recordMutation('simplify');
        this.$message.success(`已简化 ${simplifiedCount} 个面的边缘`);
      } else if (!skippedCount) {
        // 有面被跳过时不再说"边缘已足够平滑"，两条提示自相矛盾
        this.$message.info('选中面的边缘已足够平滑');
      }
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
      // 保存腿上下文守卫（P1-6，与加载腿 reloadSeq 同构）：保存 A 期间切到 B，
      // A 的响应回来不得覆盖 B 的基准版本号/画布/编辑基线；服务端已写入 A 是
      // 合法状态（按 base_revision_no 乐观锁提交），只是不再动本地 UI
      const seq = this.reloadSeq;
      const savedResultId = this.context.resultId;
      // 保存竞态双比对（2026-10-31 审计 P1）：提交快照取自请求发出瞬间；
      // editGeneration 覆盖已 record 的手势，而顶点拖拽等未结束手势的
      // draw.update 要到鼠标释放才发出、代次可能未变，需快照比对兜底
      const submittedDrawJson = this.currentDrawJson();
      const editSeq = this.editGeneration;
      // 保存 422 定位用（E2-d）：后端 details.feature_index 是提交 FC 的 0 起下标，
      // 与 draw.getAll().features 同序——记下提交瞬间各下标对应的画布要素 id，
      // 保存期间画布若有增删，按 id 仍能找回出错的那个面
      const submittedDrawIds = this.draw.getAll().features.map((feature) => feature.id);
      // 保存提交边界（2026-10-31 复审 P2）：提交后的首个 vertex 手势不得被
      // coalesce 窗口合并进提交前手势，否则历史栈中没有保存边界快照，
      // markSaved 只能退化清栈，保存期间的修改无法撤销
      this.history.markSubmitBoundary();
      this.saving = true;
      const stillOwnsState = () =>
        this.reloadSeq === seq && this.context?.resultId === savedResultId;
      try {
        const saved = await saveClassificationRevision(this.context.projectId, savedResultId, {
          base_revision_no: this.result.current_revision_no,
          feature_collection: featureCollection,
        });
        if (!stillOwnsState()) return;
        this.result.current_feature_collection = saved.feature_collection;
        this.result.current_revision_no = saved.current_revision_no;
        if (this.editGeneration !== editSeq || this.currentDrawJson() !== submittedDrawJson) {
          // 保存期间画布又有新修改：保留画布与本地手势（含未结束手势），
          // 仅把已落库快照登记为新基线（撤销不得穿越保存边界），提示再次保存
          // 服务端为提交时的新面（client-*）分配了正式 manual-* ID（既有 ID
          // 保留、逐要素按提交顺序规范化）——必须同步到画布与历史快照，否则
          // 再次保存会把同一面当新面，破坏跨版本图斑追踪（2026-10-31 复审 P2）
          const assignedIds = buildAssignedFeatureIdMap(featureCollection, saved.feature_collection);
          if (assignedIds.size) {
            this.applyFeatureIdMap(assignedIds);
            this.history.migrateIds(assignedIds);
          }
          this.history.markSaved(rewriteFeatureCollectionJson(submittedDrawJson, assignedIds));
          this.syncHistoryState();
          // 提示先于任何 await 弹出（2026-11-23 P1）：此前先等版本刷新再提示，
          // 刷新期间用户切到别的成果时，归属检查会把"已保存"提示吞掉
          this.$message.warning(`已保存为 V${saved.current_revision_no}；保存期间又有新的修改，请再次保存。`);
          // 保存事务已成功：先解除 saving（工具栏立即可用），再发不阻塞的版本刷新
          // ——版本接口慢时按钮不再被占到刷新结束
          this.saving = false;
          this.loadRevisions().catch(() => {
            if (!stillOwnsState()) return;
            this.$message.warning(`已保存为 V${saved.current_revision_no}；版本列表刷新失败，可手动刷新查看。`);
          });
          return;
        }
        this.draw.set(saved.feature_collection || emptyFeatureCollection());
        // 新版本即新基线：编辑历史作废，撤销只服务当前版本的未保存手势
        this.resetEditHistory();
        this.setSelectedDrawIds([]);
        // 问题11（2026-11-15，自 B2 候选移植 2026-11-23）：保存成功与列表刷新
        // 分离——刷新失败不伪装成保存失败，不诱导用户重复保存
        this.$message.success(`已保存为 V${saved.current_revision_no}`);
        // 同上：先解除 saving，再发不阻塞的版本刷新
        this.saving = false;
        this.loadRevisions().catch(() => {
          if (!stillOwnsState()) return;
          this.$message.warning(`已保存为 V${saved.current_revision_no}；版本列表刷新失败，可手动刷新查看。`);
        });
      } catch (error) {
        if (!stillOwnsState()) return;
        if (error?.status === 409) {
          this.$message.error('该成果已有新版本，请重新加载后再编辑；系统不会自动覆盖他人的修改。');
        } else if (error?.status === 422) {
          this.locateSaveValidationFailure(error, submittedDrawIds);
        } else {
          this.$message.error(error?.message || '保存分类成果失败');
        }
      } finally {
        // 上下文已被更新的 reload 接管时 saving 由该 reload 复位，这里不动
        if (stillOwnsState() || this.reloadSeq === -1) {
          this.saving = false;
        }
      }
    },
    applyFeatureIdMap(idMap) {
      // 竞态分支：把服务端分配的正式 manual-* ID 写回画布要素的
      // properties.feature_id（业务 ID，与 MapboxDraw 内部要素 id 无关）。
      // setFeatureProperty 为编程式 API（suppressAPIEvents 默认 true），
      // 不触发 draw.update，不会造成额外手势记录
      if (!this.draw || !idMap.size) return;
      this.draw.getAll().features.forEach((feature) => {
        const current = feature.properties?.feature_id;
        if (typeof current === 'string' && idMap.has(current)) {
          this.draw.setFeatureProperty(feature.id, 'feature_id', idMap.get(current));
        }
      });
    },
    async loadRevisions() {
      if (!this.context) return false;
      // 响应归属检查（问题12，2026-11-15 移植；2026-11-23 P1 补全为四项）：以
      // reloadSeq 为编辑会话代次，发出前捕获会话代次、请求序号、项目与成果；返回后
      // 四项全部一致才写 revisions 并返回 true，否则返回 false 且不动任何状态。
      // 此前只比后三项：同一成果"重新加载"后，本方法自己发的请求没登记进会话，
      // 更早发出的旧 loadRevisions 响应归属检查全部通过——版本列表先显示 V2
      // 又被旧响应改回 V1
      const { projectId, resultId } = this.context;
      const sessionSeq = this.reloadSeq;
      const seq = (this.revisionsLoadSeq = (this.revisionsLoadSeq || 0) + 1);
      this.revisionsLoading = true;
      let payload;
      try {
        payload = await getClassificationRevisions(projectId, resultId);
      } finally {
        // 只有仍是当前会话的最新请求才收起 loading：过期请求的 finally 不得
        // 提前收起新请求的"加载中"
        if (seq === this.revisionsLoadSeq && sessionSeq === this.reloadSeq) {
          this.revisionsLoading = false;
        }
      }
      const current = this.context;
      if (
        sessionSeq !== this.reloadSeq
        || seq !== this.revisionsLoadSeq
        || !current
        || current.projectId !== projectId
        || current.resultId !== resultId
      ) {
        return false;
      }
      this.revisions = payload?.revisions || [];
      return true;
    },
    // 保存 422 定位（E2-d，2026-11-23）：后端 details 带 feature_index（提交 FC 的
    // 0 起下标，与 draw.getAll().features 同序）与 reason。此前只回显"无法保存：
    // 原因"，用户找不到是哪个面；现在在地图上选中并定位到出错的要素。
    // 不带 feature_index 的请求体级错误、下标非法、或该面在保存期间已被删除时，
    // 退化为只回显原因 / 序号。
    locateSaveValidationFailure(error, submittedDrawIds) {
      const reason = error?.details?.reason || error?.details?.message || error?.message;
      const featureIndex = error?.details?.feature_index;
      const total = submittedDrawIds.length;
      if (!Number.isInteger(featureIndex) || featureIndex < 0 || featureIndex >= total) {
        this.$message.error(`无法保存：${reason}`);
        return;
      }
      const targetId = submittedDrawIds[featureIndex];
      let target = null;
      try {
        target = this.draw?.get(targetId) || null;
      } catch (_) {
        // 保存期间该面已被删除：无法定位
        target = null;
      }
      if (target) {
        this.draw.changeMode('simple_select', { featureIds: [targetId] });
        // 已处于 simple_select 时 changeMode 静默改选、不发 selectionchange，同步本地选中态
        this.setSelectedDrawIds([targetId]);
        const bounds = resolveFocusBounds({ type: 'FeatureCollection', features: [target] }, null, null);
        if (bounds && this.map) {
          this.map.fitBounds(
            [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
            { padding: 80, maxZoom: 18, duration: 400 },
          );
        }
      }
      const located = target ? '，已在地图上选中并定位' : '';
      this.$message.error(`无法保存：第 ${featureIndex + 1} 个面（共 ${total} 个）${reason}${located}`);
    },
    async downloadCurrent() {
      if (!this.context) return;
      // 导出腿上下文守卫（与保存腿同构，2026-11-23 P1）：导出 A 期间切到成果 B，
      // A 的错误提示与 exporting 复位不得落到 B
      const seq = this.reloadSeq;
      const exportResultId = this.context.resultId;
      const stillOwnsState = () => this.reloadSeq === seq && this.context?.resultId === exportResultId;
      if (this.isDirty) {
        this.exportDraft(exportResultId);
        return;
      }
      this.exporting = true;
      try {
        const response = await exportClassificationResult(this.context.projectId, exportResultId);
        triggerDownload(
          URL.createObjectURL(response.data),
          filenameFromContentDisposition(response.headers?.['content-disposition']),
        );
      } catch (error) {
        if (!stillOwnsState()) return;
        this.$message.error(error?.message || '导出当前分类成果失败');
      } finally {
        // 上下文已被更新的 reload 接管时 exporting 由该 reload 复位，这里不动
        if (stillOwnsState() || this.reloadSeq === -1) {
          this.exporting = false;
        }
      }
    },
    // 草稿导出（E2-e，2026-11-23）：服务端接口导出的是已保存版本，有未保存修改时
    // 导出的不是用户看到的内容。改为在客户端导出当前画布（与保存提交同一套规范化：
    // 只保留 class_code / feature_id），文件名标明"基于 Vn 的未保存草稿"
    exportDraft(resultId) {
      let draft;
      try {
        draft = buildEditableFeatureCollection(this.draw.getAll());
      } catch (error) {
        this.$message.error(error?.message || '草稿矢量数据无效');
        return;
      }
      const objectUrl = URL.createObjectURL(
        new Blob([JSON.stringify(draft)], { type: 'application/geo+json' }),
      );
      triggerDownload(objectUrl, draftExportFilename(resultId, this.result?.current_revision_no));
      this.$message.info('已导出草稿（包含未保存的修改）；保存后导出的才是正式版本');
    },
    classColor(definition) {
      return resolveClassColor(definition);
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
/* mapbox-gl-draw 控件沿用旧 mapboxgl-ctrl 前缀类；maplibre-gl.css 只对自身
   .maplibregl-ctrl 恢复 pointer-events:auto（角容器默认 none），旧前缀组不被
   匹配 → 绘制/删除按钮可见但真实点击不可命中（H-3 真实栈发现）。此处补桥。 */
.editor-workspace :deep(.mapboxgl-ctrl-group) {
  pointer-events: auto;
}

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

.class-option:hover:not(:disabled),
.class-option.active {
  border-color: rgba(43, 182, 173, 0.38);
  background: var(--primary-hover);
}

.class-option:disabled {
  cursor: not-allowed;
  opacity: 0.55;
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

/* 版本列表加载中占位（2026-11-23 P1）：与版本条目的次要文字同一视觉层级 */
.revision-loading {
  color: var(--text-muted);
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
