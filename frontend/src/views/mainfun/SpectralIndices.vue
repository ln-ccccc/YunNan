<template>
  <div>
    <Tabinfor>
      <template #left>
        <div id="sub-title">
          光谱指数计算<i class="iconfont icon-dianji" />
        </div>
      </template>
    </Tabinfor>
    <el-divider />
    <p>
      请上传<span class="go-bold">tif/tiff遥感影像</span><i class="iconfont icon-tupiantianjia" />并选择指数类型
    </p>
    <el-row type="flex" justify="center">
      <el-col :span="24">
        <el-card style="border: 4px dashed var(--el-border-color)">
          <div v-if="fileList.length" class="clear-queue">
            <el-button type="primary" class="btn-animate2 btn-animate__surround" @click="clearQueue">
              清空影像
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
              v-if="fileList.length"
              class="selected-files"
              @click.stop
            >
              <div class="selected-files__title">
                已选择 {{ fileList.length }} 个 tif/tiff 文件
              </div>
              <div
                v-for="item in fileList.slice(0, 8)"
                :key="item.uid"
                class="selected-files__item"
              >
                {{ item.name }}
              </div>
              <div
                v-if="fileList.length > 8"
                class="selected-files__item"
              >
                其余 {{ fileList.length - 8 }} 个文件待上传
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
          <el-row justify="center" class="option-row">
            <el-form label-width="100px" inline>
              <el-form-item label="指数类型">
                <el-select v-model="indexType" style="width: 180px;">
                  <el-option label="NDVI" value="NDVI" />
                  <el-option label="NDBI" value="NDBI" />
                  <el-option label="NDWI" value="NDWI" />
                  <el-option label="NDSI" value="NDSI" />
                </el-select>
              </el-form-item>
              <el-form-item label="年份">
                <el-input v-model="year" style="width: 180px;" placeholder="例如 2024（可选）" />
              </el-form-item>
              <el-form-item label="NIR波段">
                <el-input-number v-model="bandMap.nir" :min="1" />
              </el-form-item>
              <el-form-item label="RED波段">
                <el-input-number v-model="bandMap.red" :min="1" />
              </el-form-item>
              <el-form-item label="GREEN波段">
                <el-input-number v-model="bandMap.green" :min="1" />
              </el-form-item>
              <el-form-item label="SWIR波段">
                <el-input-number v-model="bandMap.swir" :min="1" />
              </el-form-item>
            </el-form>
          </el-row>
          <div class="handle-button">
            <el-button type="primary" class="btn-animate btn-animate__shiny" @click="startCompute">
              开始计算
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <Tabinfor>
      <template #left>
        <div id="sub-title">
          结果图预览<i class="iconfont icon-dianji" />
        </div>
      </template>
      <template #right>
        <span class="go-bold">
          <i class="iconfont icon-shuaxin" style="padding-right:55px" @click="getMore"><span class="hidden-sm-and-down">点击刷新</span></i>
        </span>
      </template>
    </Tabinfor>
    <el-divider />
    <ImgShow :img-arr="imgArr" @delete-item="deleteHistoryItem" />
    <Bottominfor />
  </div>
</template>

<script>
import { createSrc, imgUpload } from "@/api/upload";
import { getUploadImg } from "@/utils/getUploadImg";
import { readProjectId } from "@/utils/interpretationContext.mjs";
import { historyDeleteOne } from "@/api/history";
import Tabinfor from "@/components/Tabinfor";
import Bottominfor from "@/components/Bottominfor";
import ImgShow from "@/components/ImgShow";

export default {
  name: "SpectralIndices",
  components: {
    Tabinfor,
    Bottominfor,
    ImgShow
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      document.querySelector(".el-main").scrollTop = 0;
    });
  },
  data() {
    return {
      fileList: [],
      imgArr: [],
      projectId: readProjectId(window.location.search),
      indexType: "NDVI",
      year: "",
      bandMap: {
        nir: 4,
        red: 3,
        green: 2,
        swir: 5
      }
    };
  },
  created() {
    this.getUploadImg("光谱指数计算");
  },
  methods: {
    createSrc,
    imgUpload,
    getUploadImg,
    historyDeleteOne,
    openFolderPicker() {
      this.$refs.folderInput && this.$refs.folderInput.click();
    },
    openFilePicker() {
      this.$refs.fileInput && this.$refs.fileInput.click();
    },
    isValidTiff(fileLike) {
      const raw = fileLike?.raw || fileLike?.file || fileLike;
      const name = String(fileLike?.relativePath || raw?.webkitRelativePath || raw?.name || "");
      const suffix = name.substring(name.lastIndexOf(".") + 1).toLowerCase();
      return ["tif", "tiff"].includes(suffix);
    },
    normalizeSelectedItems(inputFiles) {
      return inputFiles.map((item) => {
        if (item?.raw) return item;
        if (item?.file) {
          return {
            raw: item.file,
            relativePath: item.relativePath || item.file.webkitRelativePath || item.file.name,
          };
        }
        return {
          raw: item,
          relativePath: item?.webkitRelativePath || item?.name,
        };
      });
    },
    createUploadItems(files) {
      return files.map((item, index) => {
        const raw = item.raw;
        return {
          name: item.relativePath || raw.webkitRelativePath || raw.name,
          size: raw.size,
          status: "ready",
          uid: `${raw.name}-${raw.lastModified}-${index}`,
          raw,
        };
      });
    },
    replaceFileList(inputFiles) {
      const normalizedFiles = this.normalizeSelectedItems(inputFiles);
      const validFiles = normalizedFiles.filter((file) => this.isValidTiff(file));
      const invalidCount = normalizedFiles.length - validFiles.length;
      if (invalidCount > 0) {
        this.$message.warning(`已忽略 ${invalidCount} 个非 tif/tiff 文件`);
      }
      if (validFiles.length === 0) {
        this.fileList = [];
        this.$message.error("只允许上传 tif / tiff 格式,请重新上传");
        return;
      }
      this.fileList = this.createUploadItems(validFiles);
    },
    handleFileSelect(event) {
      const rawFiles = Array.from(event?.target?.files || []);
      if (rawFiles.length === 0) return;
      this.replaceFileList(rawFiles);
      event.target.value = "";
    },
    handleFolderSelect(event) {
      const rawFiles = Array.from(event?.target?.files || []);
      if (rawFiles.length === 0) return;
      this.replaceFileList(rawFiles);
      event.target.value = "";
    },
    async handleNativeDrop(event) {
      const items = Array.from(event?.dataTransfer?.items || []);
      const filesFromDrop = await this.readDroppedItems(items);
      if (filesFromDrop.length > 0) {
        this.replaceFileList(filesFromDrop);
        return;
      }
      const rawFiles = Array.from(event?.dataTransfer?.files || []);
      if (rawFiles.length > 0) {
        this.replaceFileList(rawFiles);
      }
    },
    async readDroppedItems(items) {
      if (!items.length) return [];
      const entries = items
        .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
        .filter(Boolean);
      if (!entries.length) return [];
      const files = [];
      for (const entry of entries) {
        const entryFiles = await this.walkFileTree(entry);
        files.push(...entryFiles);
      }
      return files;
    },
    walkFileTree(entry, parentPath = "") {
      if (!entry) return Promise.resolve([]);
      if (entry.isFile) {
        return new Promise((resolve) => {
          entry.file((file) => {
            resolve([{
              file,
              relativePath: parentPath ? `${parentPath}/${file.name}` : file.name,
            }]);
          }, () => resolve([]));
        });
      }
      if (!entry.isDirectory) return Promise.resolve([]);

      const directoryPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
      const reader = entry.createReader();
      return new Promise((resolve) => {
        const allEntries = [];
        const readBatch = () => {
          reader.readEntries(async (batch) => {
            if (!batch.length) {
              let files = [];
              for (const child of allEntries) {
                const childFiles = await this.walkFileTree(child, directoryPath);
                files = files.concat(childFiles);
              }
              resolve(files);
              return;
            }
            allEntries.push(...batch);
            readBatch();
          }, () => resolve([]));
        };
        readBatch();
      });
    },
    clearQueue() {
      this.fileList = [];
      this.$message.success("清除成功");
    },
    getMore() {
      this.getUploadImg("光谱指数计算");
    },
    deleteHistoryItem(item) {
      const rid = item?.id;
      if (!rid) {
        this.$message.error("记录ID缺失，无法删除");
        return;
      }
      this.$confirm("此操作将永久删除该组记录, 是否继续?", "提示", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      }).then(() => {
        return this.historyDeleteOne(rid);
      }).then(() => {
        this.$message.success("删除成功");
        this.getMore();
      }).catch(() => {});
    },
    startCompute() {
      if (!this.fileList.length) {
        this.$message.error("请先上传tif文件");
        return;
      }
      const formData = new FormData();
      for (const item of this.fileList) {
        formData.append("files", item.raw || item);
      }
      formData.append("type", "光谱指数计算");
      formData.append("keepRawTiff", "true");
      this.createSrc(formData).then((res) => {
        const uploadItems = res.data.data || [];
        const list = uploadItems.map((item) => ({
          src: item.src,
          raw_tiff_path: item.raw_tiff_path || ""
        }));
        return this.imgUpload({
          list,
          index_type: this.indexType,
          year: this.year,
          band_map: this.bandMap,
          ...(this.projectId ? { project_id: this.projectId } : {})
        }, "spectral_indices");
      }).then((res) => {
        const backendMsg = res?.data?.msg || "";
        if (backendMsg.includes("同步部分失败")) {
          this.$message.warning(backendMsg);
        } else {
          this.$message.success(backendMsg || "计算完成");
        }
        this.fileList = [];
        this.getMore();
      }).catch((err) => {
        const msg = err?.response?.data?.msg || "计算失败";
        this.$message.error(msg);
      });
    }
  }
};
</script>

<style lang="less" scoped>
* {
  font-family: SimHei sans-serif;
}

#sub-title {
  font-size: 25px;
}

#sub-title:hover:after {
  left: 0%;
  right: 0%;
  width: 220px;
}

.clear-queue {
  position: absolute;
  left: 5px;
  top: 10%;
  z-index: 100;
}

.upload-dropzone {
  min-height: 180px;
  padding: 24px 16px;
  text-align: center;
  cursor: pointer;
}

.selected-files {
  margin-top: 16px;
  text-align: left;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 6px;
  padding: 12px;
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

.option-row {
  margin-top: 25px;
}
</style>
