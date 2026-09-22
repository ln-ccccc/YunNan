<template>
  <el-card
    class="tiff-upload-card"
    style="border: 4px dashed var(--el-border-color)"
  >
    <div
      v-if="modelValue.length"
      class="clear-queue"
    >
      <el-button
        type="primary"
        class="btn-animate2 btn-animate__surround"
        @click="clearFiles"
      >
        {{ clearText }}
      </el-button>
    </div>
    <div
      class="upload-card upload-dropzone"
      @click="openFolderPicker"
      @dragover.prevent
      @dragenter.prevent
      @drop.prevent="handleNativeDrop"
    >
      <i class="iconfont icon-yunduanshangchuan" />
      <div class="el-upload__text">
        将文件夹拖到此处，或<em>点击上传整个文件夹</em>
      </div>
      <div class="el-upload__tip">
        递归读取文件夹内容，仅上传 tif / tiff
      </div>
      <div
        v-if="modelValue.length"
        class="selected-files"
        @click.stop
      >
        <div class="selected-files__title">
          已选择 {{ modelValue.length }} 个 tif/tiff 文件
        </div>
        <div
          v-for="item in modelValue.slice(0, 8)"
          :key="item.uid"
          class="selected-files__item"
        >
          {{ item.name }}
        </div>
        <div
          v-if="modelValue.length > 8"
          class="selected-files__item"
        >
          其余 {{ modelValue.length - 8 }} 个文件待上传
        </div>
      </div>
    </div>
    <el-row justify="center">
      <el-button
        plain
        size="small"
        style="margin-top: 8px;"
        @click="openFolderPicker"
      >
        选择整个文件夹
      </el-button>
      <el-button
        plain
        size="small"
        style="margin-top: 8px; margin-left: 8px;"
        @click="openFilePicker"
      >
        选择文件
      </el-button>
    </el-row>
    <input
      ref="folderInput"
      type="file"
      webkitdirectory
      multiple
      style="display: none;"
      @change="handleFolderSelect"
    >
    <input
      ref="fileInput"
      type="file"
      accept=".tif,.tiff,.TIF,.TIFF"
      multiple
      style="display: none;"
      @change="handleFileSelect"
    >
    <el-row justify="center">
      <div style="color:#909399; font-size: 12px;">
        支持拖拽文件夹、点击选择整个文件夹，自动递归过滤非 tif / tiff 文件
      </div>
    </el-row>
    <slot />
  </el-card>
</template>

<script>
import {
  createUploadItems,
  filesFromInputEvent,
  isValidTiff,
  normalizeSelectedItems,
  readDroppedItems,
} from "@/utils/tiffSelection.mjs";

// 地物分类与光谱指数两页共享的 tif 上传卡：拖拽/文件夹/文件选择、递归过滤、清空。
// fileList 归父组件所有（v-model）；选择结果经 select 事件带出（空数组=全部被拒），
// 父级据此做页面联动（裁剪预览、批量关裁剪等）。
export default {
  name: "TiffUploadCard",
  props: {
    modelValue: {
      type: Array,
      default: () => [],
    },
    clearText: {
      type: String,
      default: "清空图片",
    },
  },
  emits: ["update:modelValue", "select"],
  watch: {
    // 父级从外部清空（如裁剪完成 notvisible）时同步复位 input value，
    // 否则残留 value 会吞掉下一次选择同名文件/文件夹的 change 事件
    modelValue(next) {
      if (!next || next.length === 0) this.resetInputs();
    },
  },
  methods: {
    resetInputs() {
      if (this.$refs.folderInput) this.$refs.folderInput.value = "";
      if (this.$refs.fileInput) this.$refs.fileInput.value = "";
    },
    clearFiles() {
      this.resetInputs();
      this.$emit("update:modelValue", []);
      this.$emit("select", []);
      this.$message.success("清除成功");
    },
    openFolderPicker() {
      if (this.$refs.folderInput) {
        this.$refs.folderInput.value = "";
        this.$refs.folderInput.click();
      }
    },
    openFilePicker() {
      if (this.$refs.fileInput) {
        this.$refs.fileInput.value = "";
        this.$refs.fileInput.click();
      }
    },
    applySelection(inputFiles) {
      const normalizedFiles = normalizeSelectedItems(inputFiles);
      const validFiles = normalizedFiles.filter((file) => isValidTiff(file));
      const invalidCount = normalizedFiles.length - validFiles.length;
      if (invalidCount > 0) {
        this.$message.warning(`已忽略 ${invalidCount} 个非 tif/tiff 文件`);
      }
      const items = createUploadItems(validFiles);
      if (items.length === 0) {
        this.$message.error("只允许上传 tif / tiff 格式,请重新上传");
      }
      this.$emit("update:modelValue", items);
      this.$emit("select", items);
      return items;
    },
    handleFileSelect(event) {
      const rawFiles = filesFromInputEvent(event);
      if (rawFiles.length === 0) return;
      this.applySelection(rawFiles);
      event.target.value = "";
    },
    handleFolderSelect(event) {
      const rawFiles = filesFromInputEvent(event);
      if (rawFiles.length === 0) return;
      this.applySelection(rawFiles);
      event.target.value = "";
    },
    async handleNativeDrop(event) {
      const items = Array.from(event?.dataTransfer?.items || []);
      const filesFromDrop = await readDroppedItems(items);
      if (filesFromDrop.length > 0) {
        this.applySelection(filesFromDrop);
        return;
      }
      const rawFiles = Array.from(event?.dataTransfer?.files || []);
      if (rawFiles.length > 0) {
        this.applySelection(rawFiles);
      }
    },
  },
};
</script>

<style lang="less" scoped>
.clear-queue {
  position: absolute;
  left: 5px;
  top: 10%;
  z-index: 100;
}

.upload-dropzone {
  min-height: 132px;
  padding: 18px 16px 14px;
  text-align: center;
  cursor: pointer;
}

.upload-dropzone .iconfont {
  display: block;
  font-size: 38px;
  line-height: 1;
  margin-bottom: 8px;
  color: var(--theme--color);
}

.upload-dropzone .el-upload__text {
  font-size: 15px;
  line-height: 1.5;
}

.upload-dropzone .el-upload__tip {
  margin-top: 4px;
  line-height: 1.4;
}

.upload-dropzone :deep(.el-button) {
  margin-top: 8px !important;
}

.upload-dropzone > .el-row {
  margin-top: 8px;
}

.upload-dropzone > .el-row p {
  margin: 4px 0;
}

.upload-dropzone :deep(.el-input) {
  margin: 2px 0 6px;
}

.upload-dropzone + .el-row,
.upload-dropzone ~ .el-row {
  margin-top: 8px;
}

.selected-files {
  margin-top: 10px;
  text-align: left;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 6px;
  padding: 10px;
}

.selected-files__title {
  font-weight: 700;
  margin-bottom: 8px;
}

.selected-files__item {
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}
</style>
