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

      <!-- 被取代提示（2026-11-24）：概览、台账、变化矩阵、溯源只认"同矿山同年 id 最大的
           那条"成果。生产 3036 条可编辑成果里 2387 条已被更新的推理成果取代，此前编辑它们
           没有任何提示，保存成功却哪里都不体现。与上面的覆盖提示一样独立成链 -->
      <el-alert
        v-if="result.effective === false"
        class="editor-alert"
        type="warning"
        :closable="false"
        title="该成果已被同矿山同年更新的成果取代"
        description="这里保存的修订只保留在本成果自己的版本记录里，不会体现在概览、台账、变化矩阵与溯源中。要修正统计口径，请编辑同一矿山同年里没有“已被取代”标记的那一条成果。"
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
          <el-button :disabled="(!canUndo && !sketching) || saving" plain title="Ctrl+Z" @click="undoLastEdit">撤销</el-button>
          <el-button :disabled="!canRedo || saving" plain title="Ctrl+Y" @click="redoLastEdit">重做</el-button>
          <el-button :disabled="!isDirty || saving" plain @click="discardAllEdits">放弃全部修改</el-button>
          <el-button
            :loading="merging"
            :disabled="selectedDrawIds.length < 2 || mixedClassSelection || saving || merging"
            :title="mixedClassSelection ? '仅支持合并同地类的面' : '合并同地类的相邻选中面'"
            plain
            @click="mergeSelectedFeatures"
          >
            合并选中
          </el-button>
          <el-button
            :loading="simplifying"
            :disabled="!selectedDrawIds.length || saving || simplifying"
            plain
            title="轻度简化选中面边缘（≈1m 容差）"
            @click="simplifySelectedFeatures"
          >
            消除锯齿
          </el-button>
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
          <!-- 键盘操作说明（2026-11-24）：此前的说明一个键都没提。放在版本记录下方——
               那里有整块空白；放进左侧地类面板会被面板高度裁掉、要在面板里滚动才看得到 -->
          <details class="key-hints">
            <summary>键盘操作</summary>
            <ul>
              <li><kbd>回车</kbd> / <kbd>双击</kbd> / 单击起点：完成绘制；<kbd>Esc</kbd>：取消绘制、退出顶点编辑或取消选中</li>
              <li>绘制中 <kbd>Backspace</kbd> / <kbd>Delete</kbd> / <kbd>Ctrl</kbd>+<kbd>Z</kbd>：退回上一个点</li>
              <li>顶点模式下：单击顶点后拖动，<kbd>Delete</kbd> 删除该顶点；点边上的小圆点可加顶点</li>
              <li><kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd>：撤销 / 重做</li>
            </ul>
          </details>
        </aside>
      </div>
    </template>
  </section>
</template>

<script>
import { Map, NavigationControl } from 'maplibre-gl';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import 'maplibre-gl/dist/maplibre-gl.css';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';

import {
  exportClassificationResult,
  getClassificationResult,
  getClassificationRevisions,
  saveClassificationRevision,
  simplifyClassificationGeometries,
  unionClassificationGeometries,
  validateClassificationGeometries,
} from '@/api/classificationResults';
import global from '@/global';
import {
  DRAW_POINT_STYLES,
  SKETCH_MODE,
  basemapCoverageNotice,
  buildAssignedFeatureIdMap,
  buildEditableFeatureCollection,
  createGuardedDirectSelect,
  createGuardedSimpleSelect,
  createStepBackDrawPolygon,
  describeVertexDeletionBlockers,
  draftExportFilename,
  isEditableVectorStatus,
  resolveFocusBounds,
  resolveSecureTileTemplate,
  resolveTileZoomRange,
  rewriteFeatureCollectionJson,
} from '@/utils/classificationEditor.mjs';
import { createEditorHistory } from '@/utils/classificationEditorHistory.mjs';
import { createValidationScheduler, matchVerdicts } from '@/utils/classificationEditorValidation.mjs';
import { saveBlobAsFile } from '@/utils/blobDownload.mjs';
import {
  classColor as resolveClassColor,
  classColorExpression,
} from '@/utils/classificationColors.mjs';
import { readClassificationResultContext } from '@/utils/classificationResultContext.mjs';


const FALLBACK_CENTER = [104.2, 25.1];
const FALLBACK_ZOOM = 5;
// 编辑完成后多久，把这段时间内新建或改动过的面一次性送去后端校验。mapbox-gl-draw 的
// draw.update 只在鼠标释放、增删顶点时各发一次，拖动过程中没有事件，不需要再按鼠标事件节流
const VALIDATION_DEBOUNCE_MS = 200;
// 待校验的面是刚画出来的，还是改过顶点 / 拖动过的——决定越界提示里的下一步
const VALIDATION_ACTION_CREATE = 'create';
const VALIDATION_ACTION_UPDATE = 'update';
const POLYGON_GEOMETRY_TYPES = new Set(['Polygon', 'MultiPolygon']);
// 合并请求失败、且后端没有给出具体原因时的兜底文案
const MERGE_FAILURE_FALLBACK = '几何无效或边界过于复杂，可手动调整后重试';

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
      id: DRAW_POINT_STYLES.vertex.id,
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex'], ['!=', 'mode', 'simple_select']],
      paint: { 'circle-radius': DRAW_POINT_STYLES.vertex.radius, 'circle-color': '#ffffff' },
    },
    {
      id: 'gl-draw-vertex-inner',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex'], ['!=', 'mode', 'simple_select']],
      paint: { 'circle-radius': 5, 'circle-color': '#f59e0b' },
    },
    {
      id: DRAW_POINT_STYLES.midpoint.id,
      type: 'circle',
      filter: ['all', ['==', 'meta', 'midpoint']],
      paint: { 'circle-radius': DRAW_POINT_STYLES.midpoint.radius, 'circle-color': '#f59e0b' },
    },
  ];
}

function validBounds(value) {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) return null;
  const [west, south, east, north] = value;
  if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) return null;
  return value;
}

function createClientFeatureId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `client-${crypto.randomUUID()}`;
  }
  return `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// 两个几何的坐标是否逐一相同。合并 / 简化从同步计算变成网络请求后（2026-11-24），
// 画布在请求期间仍可编辑：某个面被拖动、删除或被撤销 / 重做换掉时，响应对应的是旧几何，
// 不能套用到新画布上（否则刚做的修改被悄悄覆盖）。这里只做逐值相等比较，不做几何判定
function sameGeometry(left, right) {
  return Boolean(left && right)
    && left.type === right.type
    && JSON.stringify(left.coordinates) === JSON.stringify(right.coordinates);
}

// 请求失败时给用户看的原因：优先后端 details.reason（422 时说明是哪个面、为什么），
// 其次是响应拦截器给出的 message
function requestFailureReason(error, fallback) {
  return error?.details?.reason || error?.message || fallback;
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
      // 合并 / 简化是网络请求（2026-11-24）：在途期间按钮禁用并阻止重入
      merging: false,
      simplifying: false,
      isDirty: false,
      activeClassCode: 0,
      selectedDrawIds: [],
      // 选中面的地类集合（合并按钮置灰依据，E2-b）：与 selectedDrawIds 同步维护，
      // 只经 setSelectedDrawIds 写入
      selectedClassCodes: [],
      // 绘制库当前模式（draw.modechange 同步）：为 draw_polygon 时表示"有画到一半的草稿"。
      // 撤销按钮据此保持可用（绘制中撤销=退回上一个顶点），保存/导出据此拦截
      drawMode: 'simple_select',
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
    // 绘制进行中（响应式，供模板用；判定口径同 isSketching）
    sketching() {
      return this.drawMode === SKETCH_MODE;
    },
  },
  watch: {
    '$route.fullPath'() {
      this.reloadResult();
    },
  },
  created() {
    // 编辑完成后的权威校验（2026-11-24）：面的合法性由后端几何引擎判定，与保存共用同一个
    // 判定函数。此前前端自带三套几何判定（turf booleanWithin、采样式越界检测、手写的有效性
    // 镜像），真实数据上 33% 的图斑被误报越界。收集 + 防抖 + 排序在
    // utils/classificationEditorValidation.mjs，这里只注入取几何发请求与中文提示。
    // 不放进 data()：它不需要响应式，放进去反而会让 Vue 把它的内部集合包成响应式代理
    this.validationScheduler = createValidationScheduler({
      delayMs: VALIDATION_DEBOUNCE_MS,
      validate: (drawIds) => this.validateDrawGeometries(drawIds),
      onVerdicts: (verdicts) => this.reportGeometryVerdicts(verdicts),
    });
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
      // 切换成果时复位：在途旧保存/导出/合并/简化的 finally 不再清理新上下文的标志
      this.saving = false;
      this.exporting = false;
      this.merging = false;
      this.simplifying = false;
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
          // 底图的覆盖范围（清单 bounds = 底图资源登记的范围）同样交给 source：视野里超出覆盖
          // 的那部分不再逐格请求不存在的瓦片。地图渲染本来正常，但浏览器控制台每次打开都刷
          // 几十条 404（真实编辑测试一轮 192 条，2026-11-24 记录），范围里的瓦片照常请求
          ...(bounds ? { bounds } : {}),
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
        // 顶点删除防护挂在真实入口（2026-11-23 P1）：工具栏删除按钮与画布上的 Delete/Backspace
        // （2026-11-24 起经容器 keydown 转到同一个 draw.trash()，见下）都汇到 direct_select.onTrash。
        // 此前只在地图容器 click 捕获阶段拦截：键盘完全绕过；且只判"面当前是否 ≤3 顶点"，四边形
        // Shift 选两个顶点再删除会整面被删；对 MultiPolygon 把环数当顶点数，合法删除被误拦
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
            // 命中规则修正（2026-11-24）：库的"小面优先命中"从未生效，叠在一起或贴着边的
            // 图斑里点小图斑选中的常常是大图斑；真实成果默认视野下 57 个图斑只有 31 个能单独点中
            simple_select: createGuardedSimpleSelect(MapboxDraw.modes.simple_select),
            // 绘制中的删除按钮 / Delete / Backspace / Ctrl+Z 退回上一个顶点，而不是丢弃整个草稿
            draw_polygon: createStepBackDrawPolygon(MapboxDraw.modes.draw_polygon),
          },
        });
        this.map.addControl(this.draw, 'top-right');
        // 顶点模式下没有选中顶点时，删除按钮与 Delete/Backspace 都是无操作（mapbox-gl-draw 1.5
        // 行为，2026-11 验收遗留）：没有效果也没有提示，用户以为删除坏了。这里给出引导。
        const guideTrashWithoutVertex = () => {
          if (!this.draw || this.draw.getMode() !== 'direct_select') return;
          const selectedPoints = this.draw.getSelectedPoints?.();
          const hasSelectedVertex = !!(selectedPoints && selectedPoints.features && selectedPoints.features.length);
          if (!hasSelectedVertex) {
            this.$message.info(
              '当前处于顶点编辑模式：请先点击选中某个顶点，删除按钮或 Delete 键才会移除该顶点；要删除整个面，请按 Esc 退出顶点编辑后单击选中面再删除',
            );
          }
        };
        // 注意必须挂容器捕获阶段：draw 控件自身的按钮 click 监听会拦截同元素上
        // 后注册的监听（浏览器实测 click 探针不触发），按钮上 addEventListener 是死代码。
        // 顶点删除防护在 direct_select.onTrash（见上），这里只负责引导提示。
        this.map.getContainer().addEventListener(
          'click',
          (e) => {
            if (!(e.target instanceof Element) || !e.target.closest('.mapbox-gl-draw_trash')) return;
            guideTrashWithoutVertex();
          },
          true,
        );
        // 画布上的按键统一在这里补齐。mapbox-gl-draw 自己的键盘处理只认 class 含 mapboxgl-canvas
        // 的目标，而这里是 MapLibre 的 maplibregl-canvas——Esc、Delete、Backspace 在画布上从来没
        // 有效果。Esc 于 2026-11-23 补齐；Delete/Backspace 于 2026-11-24 在真实成果上测出（此前
        // "Delete 键删除被拦且有说明"的验收结论是上一步按钮提示残留造成的假阳性）。
        // 删除走 draw.trash()：与工具栏删除按钮汇合到同一条入口，顶点删除防护对按键同样生效。
        // 只认以画布为目标的事件：焦点在按钮等处时按键不做删除。
        const TRASH_KEYS = new Set(['Delete', 'Backspace']);
        this.map.getContainer().addEventListener(
          'keydown',
          (e) => {
            if (!this.draw) return;
            if (e.key === 'Escape' && this.draw.getMode() === 'direct_select') {
              const selectedId = this.draw.getSelected?.()?.features?.[0]?.id;
              this.draw.changeMode('simple_select', selectedId != null ? { featureIds: [selectedId] } : {});
              return;
            }
            // 普通选择下 Esc 清除选中（库自带的键盘处理同样到不了这里）。必须传空对象：
            // 上游 onSetup 直接读 opts.featureIds，不传会抛 TypeError。
            // 已处于 simple_select 时库只静默改选中、不发 draw.selectionchange
            // （api.changeMode 的就近分支），本地选中态必须自己同步——否则地图上的高亮
            // 已经消失，工具栏仍显示"已选择"，点地类会改到一个不再选中的面
            if (e.key === 'Escape' && this.draw.getMode() === 'simple_select' && this.selectedDrawIds.length) {
              this.draw.changeMode('simple_select', {});
              this.setSelectedDrawIds(this.draw.getSelectedIds());
              return;
            }
            if (!TRASH_KEYS.has(e.key) || !(e.target instanceof HTMLCanvasElement)) return;
            e.preventDefault();
            guideTrashWithoutVertex();
            this.draw.trash();
          },
        );
        // ROI 边界叠加（2026-11-13 风险复盘）：此前绘制越界只能等保存 422 才暴露，现把矿山
        // 边界画在画布上；画完 / 拖完顶点后由后端校验并即时提示（见 handleDrawCreate）
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
        this.drawMode = this.draw.getMode();
        this.map.on('draw.modechange', (event) => { this.drawMode = event.mode; });
        this.map.on('draw.create', this.handleDrawCreate);
        this.map.on('draw.update', this.handleDrawUpdate);
        this.map.on('draw.delete', this.handleDrawDelete);
        this.map.on('draw.selectionchange', this.handleSelectionChange);
        this.fitToCurrentFeatures(bounds);
      });
    },
    destroyMap() {
      // 待校验的 draw id 随画布一起作废，在途的校验响应也不再提示：切换成果或卸载后，
      // 旧成果的"超出 ROI"提示不得弹到新成果上
      if (this.validationScheduler) this.validationScheduler.cancel();
      if (this.map) {
        this.map.remove();
      }
      this.map = null;
      this.draw = null;
      this.tileTemplate = null;
      this.drawMode = 'simple_select';
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
      // 图斑与 ROI 是"要看的内容"：打开时让它铺满画布，最多放大到 20 级（约 0.13 米/像素，
      // 已高于生产影像的最高分辨率 0.1 米）。此前封顶 16 级——那是按合成的大矿山定的；
      // 生产的矿山只有 60 ~ 320 米，16 级下整座矿在画布上只占几十个像素（2026-11-24 真实数据），
      // 用户要先手动放大十来格才能编辑。manifest 只是底图范围，保持原参数
      this.map.fitBounds(
        [[focus[0], focus[1]], [focus[2], focus[3]]],
        contentBounds ? { padding: 64, maxZoom: 20, duration: 0 } : { padding: 64, maxZoom: 15, duration: 0 },
      );
    },
    resetDrawing() {
      if (!this.draw || !this.result) return;
      // 画到一半的草稿也在重置之列。必须先结束绘制模式再 draw.set：直接 set 会把草稿
      // 从库里删掉，而绘制模式仍在引用它——之后点击没有反应、回车时草稿凭空消失
      // （2026-11-24 通读绘制库发现）。结束时够三个点的草稿会先成为新面，随即被下面的
      // 重置清掉；suppressDrawEvents 让这一步不弹提示、不记历史
      if (this.draw.getMode() === SKETCH_MODE) {
        this.suppressDrawEvents = true;
        try {
          this.draw.changeMode('simple_select', {});
        } finally {
          this.suppressDrawEvents = false;
        }
      }
      // 画布即将回到已保存的状态：之前排上、在途的校验都是对着被丢弃的编辑做的，一并作废，
      // 免得放弃修改之后才弹"超出 ROI"
      if (this.validationScheduler) this.validationScheduler.cancel();
      this.draw.set(this.result.current_feature_collection || emptyFeatureCollection());
      this.resetEditHistory();
      this.setSelectedDrawIds([]);
    },
    // 是否有画到一半的草稿（绘制模式进行中）
    isSketching() {
      return Boolean(this.draw) && this.draw.getMode() === SKETCH_MODE;
    },
    // 草稿没有地类、环也不闭合：读整幅画布（保存、导出草稿）之前必须先完成或取消绘制
    warnSketchInProgress() {
      this.$message.warning('有正在绘制、还没完成的面：按回车或双击完成绘制，按 Esc 取消，然后再保存或导出');
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
      if (!this.draw) return;
      // 绘制进行中：撤销 = 退回刚放置的顶点（GIS 惯例），草稿已空则退出绘制。
      // 这里绝不能 draw.set()：草稿会被删出库而绘制模式仍在引用它（见 resetDrawing）
      if (this.draw.getMode() === SKETCH_MODE) {
        this.draw.trash();
        return;
      }
      if (!this.history?.canUndo) return;
      const previous = this.history.undo(this.currentDrawJson());
      if (!previous) return;
      this.draw.set(JSON.parse(previous));
      this.setSelectedDrawIds([]);
      this.syncHistoryState();
    },
    redoLastEdit() {
      // 绘制进行中重做没有意义，也不能 draw.set()（同撤销）
      if (!this.draw || this.draw.getMode() === SKETCH_MODE || !this.history?.canRedo) return;
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
      this.queueGeometryValidation(event.features, VALIDATION_ACTION_CREATE);
    },
    handleDrawUpdate(event) {
      // 事件参数必须接收（2026-11-23 修复）：此前缺参，body 引用 event 抛
      // ReferenceError，顶点拖动的 ROI 即时警告从未生效（P2-1）
      if (this.suppressDrawEvents) return;
      this.recordMutation('vertex');
      this.queueGeometryValidation(event?.features, VALIDATION_ACTION_UPDATE);
    },
    // 编辑完成后的权威校验（2026-11-24）：合法性（是否超出矿山边界、几何是否有效）由后端
    // 几何引擎判定，与保存共用同一个判定函数，前端不再自带任何几何算法。这里只把本次事件里
    // 的面记入待校验集合，防抖后一次性送出；请求失败静默——保存时后端仍会校验
    queueGeometryValidation(features, action) {
      if (!this.validationScheduler) return;
      const drawIds = (Array.isArray(features) ? features : [])
        .map((feature) => feature?.id)
        .filter((drawId) => drawId !== undefined && drawId !== null);
      if (!drawIds.length) return;
      this.validationScheduler.request(drawIds, action);
    },
    // 调度器的 validate：按 draw id 取此刻的几何发请求。期间已被删除的 id 跳过。
    // 返回 null 表示没有结论（会话已换），调度器不提示；请求失败由调度器静默吞掉
    async validateDrawGeometries(drawIds) {
      // 归属检查的参照（与 loadRevisions / saveDrawing 同构）：发出瞬间捕获会话代次与成果，
      // 返回后不一致即丢弃——A 成果的提示不得弹到已切换的 B 成果上
      const sessionSeq = this.reloadSeq;
      const { projectId, resultId } = this.context || {};
      const ids = [];
      const geometries = [];
      (drawIds || []).forEach((drawId) => {
        let geometry = null;
        try {
          geometry = this.draw?.get(drawId)?.geometry || null;
        } catch (_) {
          geometry = null;
        }
        if (!geometry) return;
        ids.push(drawId);
        geometries.push({ type: geometry.type, coordinates: geometry.coordinates });
      });
      if (!ids.length) return null;
      // 接口返回的是 { items } 而不是数组；结论必须与送审的面一一对应才采用（见 matchVerdicts）
      const payload = await validateClassificationGeometries(projectId, resultId, geometries);
      if (
        sessionSeq !== this.reloadSeq
        || sessionSeq === -1
        || this.context?.projectId !== projectId
        || this.context?.resultId !== resultId
      ) {
        return null;
      }
      const items = matchVerdicts(payload, ids);
      return items ? { ids, items } : null;
    },
    // 调度器的 onVerdicts：只给不合法的面分类提示。items 按下标对应送审的面，
    // contexts[i] 是该面最近一次编辑是新建（create）还是改动（update）
    reportGeometryVerdicts({ items, contexts }) {
      // 越界的面按"下一步"分组计数；其余不合法项（自相交等）合并成一条
      const outsideActions = [];
      let invalidCount = 0;
      (items || []).forEach((item, index) => {
        if (!item || item.ok) return;
        if (item.code === 'OUTSIDE_ROI') {
          outsideActions.push(contexts[index]);
        } else {
          invalidCount += 1;
        }
      });
      if (outsideActions.length) {
        // 刚画出来的面：拖回界内或删除；改顶点改出去的：调整顶点位置。
        // 同一批里两种都有时取后者（对新建的面同样成立）
        const allCreated = outsideActions.every((action) => action === VALIDATION_ACTION_CREATE);
        const hint = allCreated ? '，请拖回界内或删除' : '，请调整顶点位置';
        this.$message.warning(`${outsideActions.length} 个面超出成果 ROI（矿山边界），保存时将被拒绝${hint}`);
      }
      if (invalidCount) {
        this.$message.warning(`${invalidCount} 个面几何无效（如自相交），保存时将被拒绝，请调整顶点位置`);
      }
    },
    handleDrawDelete() {
      if (this.suppressDrawEvents) return;
      this.setSelectedDrawIds([]);
      this.recordMutation('delete');
    },
    handleSelectionChange(event) {
      this.setSelectedDrawIds((event.features || []).map((feature) => feature.id));
    },
    // 请求发出时记下的那个面，此刻是否还在画布上且几何没变：在则返回画布上的现状，否则 null
    unchangedLiveFeature(feature) {
      let live = null;
      try {
        live = this.draw?.get(feature.id) || null;
      } catch (_) {
        live = null;
      }
      return live && sameGeometry(live.geometry, feature.geometry) ? live : null;
    },
    // 合并（2026-11-24 起走后端）：@turf/union 换成后端几何引擎，合并结果一定通过了保存
    // 同款校验，前端不再做结构 / 有效性判定
    async mergeSelectedFeatures() {
      if (!this.draw || this.selectedDrawIds.length < 2 || this.merging) return;
      const selected = this.selectedDrawIds
        .map((id) => {
          try {
            return this.draw.get(id);
          } catch (_) {
            return null;
          }
        })
        .filter((feature) => feature && POLYGON_GEOMETRY_TYPES.has(feature.geometry?.type));
      if (selected.length < 2) {
        this.$message.warning('选中的可合并面不足两个');
        return;
      }
      const classCodes = new Set(selected.map((feature) => feature.properties?.class_code));
      if (classCodes.size > 1) {
        this.$message.warning('仅支持合并同地类的面；不同地类请分别合并');
        return;
      }
      // 合并腿上下文守卫（与保存 / 导出腿同构）：请求期间切换成果，旧成果的响应不得动画布、不提示
      const seq = this.reloadSeq;
      const mergeResultId = this.context?.resultId;
      const stillOwnsState = () => this.reloadSeq === seq && this.context?.resultId === mergeResultId;
      this.merging = true;
      try {
        const merged = await unionClassificationGeometries(
          this.context.projectId,
          mergeResultId,
          selected.map((feature) => feature.geometry),
        );
        if (!stillOwnsState()) return;
        if (!merged?.geometry) throw new Error(MERGE_FAILURE_FALLBACK);
        // 请求期间画布没有冻结：所选的面里任何一个被拖动、删除或被撤销 / 重做换掉，
        // 合并结果对应的都是旧几何——不套用，免得覆盖刚做的修改
        const live = selected.map((feature) => this.unchangedLiveFeature(feature));
        if (live.some((feature) => !feature)) {
          this.$message.warning('合并期间所选的面已被修改，本次没有合并；请确认后重试');
          return;
        }
        // 地类取此刻画布上的值（请求期间可能改过类），同地类的前提对着现状再核一次
        const liveClassCodes = new Set(live.map((feature) => feature.properties?.class_code));
        if (liveClassCodes.size > 1) {
          this.$message.warning('仅支持合并同地类的面；不同地类请分别合并');
          return;
        }
        const classCode = live[0].properties?.class_code ?? this.activeClassCode;
        this.suppressDrawEvents = true;
        try {
          // 先 add 后 delete：add 失败（无效几何）时原图不被破坏，catch 提示与实际一致
          this.draw.add({
            type: 'Feature',
            id: createClientFeatureId(),
            properties: { class_code: classCode, feature_id: createClientFeatureId() },
            geometry: merged.geometry,
          });
          this.draw.delete(selected.map((feature) => feature.id));
        } finally {
          this.suppressDrawEvents = false;
        }
        this.setSelectedDrawIds([]);
        this.recordMutation('merge');
        this.$message.success(`已合并为 1 个面（原 ${selected.length} 个）`);
      } catch (error) {
        if (!stillOwnsState()) return;
        this.$message.error(`合并失败：${requestFailureReason(error, MERGE_FAILURE_FALLBACK)}`);
      } finally {
        // 上下文已被更新的 reload 接管时 merging 由该 reload 复位，这里不动
        if (stillOwnsState() || this.reloadSeq === -1) {
          this.merging = false;
        }
      }
    },
    // 消除锯齿（2026-11-24 起走后端）：简化与"简化后是否仍合法"都由后端几何引擎完成，
    // changed 的结果一定通过了保存同款校验；前端只按 outcome 替换画布，不做任何几何判定
    async simplifySelectedFeatures() {
      if (!this.draw || !this.selectedDrawIds.length || this.simplifying) return;
      const targets = this.selectedDrawIds
        .map((id) => {
          try {
            return this.draw.get(id);
          } catch (_) {
            return null;
          }
        })
        .filter((feature) => feature && POLYGON_GEOMETRY_TYPES.has(feature.geometry?.type));
      if (!targets.length) {
        this.$message.info('选中面的边缘已足够平滑');
        return;
      }
      // 简化腿上下文守卫（同上）
      const seq = this.reloadSeq;
      const simplifyResultId = this.context?.resultId;
      const stillOwnsState = () => this.reloadSeq === seq && this.context?.resultId === simplifyResultId;
      this.simplifying = true;
      try {
        // 容差用后端默认（≈1m）：消除栅格掩膜边缘锯齿而不明显改变地物形状
        const simplified = await simplifyClassificationGeometries(
          this.context.projectId,
          simplifyResultId,
          targets.map((feature) => feature.geometry),
        );
        if (!stillOwnsState()) return;
        let simplifiedCount = 0;
        let skippedCount = 0;
        // 请求期间被改动的面：后端简化的是旧几何，不套用
        let changedMeanwhileCount = 0;
        this.suppressDrawEvents = true;
        try {
          // 返回项与入参同序。changed → 用后端给的几何替换原面（properties 原样保留）；
          // rejected（简化后会超出矿山边界或几何无效）→ 跳过，原面保持不变；unchanged → 不动
          (simplified?.items || []).forEach((item, index) => {
            const target = targets[index];
            if (!target || item?.outcome !== 'changed') {
              if (item?.outcome === 'rejected') skippedCount += 1;
              return;
            }
            // 请求期间这个面被拖动、删除或被撤销 / 重做换掉：结论对应的是旧几何，不替换
            const live = this.unchangedLiveFeature(target);
            if (!live) {
              changedMeanwhileCount += 1;
              return;
            }
            try {
              // 先 add 后 delete（同 merge 原子化策略）；单面失败跳过不中断整批
              this.draw.add({
                type: 'Feature',
                id: createClientFeatureId(),
                properties: live.properties,
                geometry: item.geometry,
              });
              this.draw.delete(target.id);
              simplifiedCount += 1;
            } catch (_) {
              skippedCount += 1;
            }
          });
        } finally {
          this.suppressDrawEvents = false;
        }
        if (skippedCount) {
          this.$message.warning(`${skippedCount} 个面简化后会超出矿山边界或几何无效，已跳过，原面保持不变`);
        }
        if (changedMeanwhileCount) {
          this.$message.info(`${changedMeanwhileCount} 个面在简化期间被修改，本次没有简化；需要的话请重新选择`);
        }
        if (simplifiedCount) {
          this.setSelectedDrawIds([]);
          this.recordMutation('simplify');
          this.$message.success(`已简化 ${simplifiedCount} 个面的边缘`);
        } else if (!skippedCount && !changedMeanwhileCount) {
          // 有面被跳过时不再说"边缘已足够平滑"，两条提示自相矛盾
          this.$message.info('选中面的边缘已足够平滑');
        }
      } catch (error) {
        if (!stillOwnsState()) return;
        this.$message.error(`简化失败：${requestFailureReason(error, '几何服务暂时不可用')}`);
      } finally {
        // 上下文已被更新的 reload 接管时 simplifying 由该 reload 复位，这里不动
        if (stillOwnsState() || this.reloadSeq === -1) {
          this.simplifying = false;
        }
      }
    },
    async saveDrawing() {
      if (!this.context || !this.result || !this.draw) return;
      if (this.isSketching()) {
        this.warnSketchInProgress();
        return;
      }
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
      // 保存期间用户可能又开始画面：此时切换模式会把草稿结束或丢弃，只提示、不定位
      const sketching = this.isSketching();
      if (target && !sketching) {
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
      const located = target && !sketching ? '，已在地图上选中并定位' : '';
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
        saveBlobAsFile(
          response.data,
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
      if (this.isSketching()) {
        this.warnSketchInProgress();
        return;
      }
      let draft;
      try {
        draft = buildEditableFeatureCollection(this.draw.getAll());
      } catch (error) {
        this.$message.error(error?.message || '草稿矢量数据无效');
        return;
      }
      saveBlobAsFile(
        new Blob([JSON.stringify(draft)], { type: 'application/geo+json' }),
        draftExportFilename(resultId, this.result?.current_revision_no),
      );
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

/* 键盘操作说明（2026-11-24）：默认收起，只占一行；展开后用版本记录下方的空白 */
.key-hints {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border-light);
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.7;
}

.key-hints summary {
  color: var(--text-secondary);
  font-weight: 600;
  cursor: pointer;
}

.key-hints ul {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.key-hints li + li {
  margin-top: 4px;
}

.key-hints kbd {
  display: inline-block;
  padding: 0 5px;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 11px;
  line-height: 18px;
}

.map-card {
  position: relative;
  min-height: 650px;
}

.classification-map {
  width: 100%;
  min-height: 650px;
}

/* 纯文字提示，没有可点内容：不拦截鼠标。否则被它盖住的那块地图上点击画不出顶点、
   选不中图斑，且没有任何反馈（2026-11-24 真实浏览器回归抓到；没有底图的项目一直显示它） */
.map-notice {
  position: absolute;
  pointer-events: none;
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
    grid-column: 1 - / -1;
  }

  .map-card,
  .classification-map {
    min-height: 500px;
  }
}
</style>
