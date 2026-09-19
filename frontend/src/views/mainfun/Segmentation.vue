<template>
  <div>
    <Tabinfor>
      <template #left>
        <div id="sub-title">
          地物分类
        </div>
      </template>
    </Tabinfor>
    <el-divider />

    <p>
      请上传<span class="go-bold">tif/tiff遥感影像</span>（用于KML矿山定位）
    </p>

    <el-row
      type="flex"
      justify="space-evenly"
    >
      <el-col :span="24">
        <el-card style="border: 4px dashed var(--el-border-color)">
          <div
            v-if="fileList.length"
            class="clear-queue"
          >
            <el-button
              type="primary"
              class="btn-animate2 btn-animate__surround"
              @click="clearQueue"
            >
              清空图片
            </el-button>
          </div>
          <div
            class="upload-card upload-dropzone"
            @click="fileClick"
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
              @click="fileClick"
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

          <el-row justify="center">
            <p>
              <label class="prehandle-label container">
                <input
                  ref="cut"
                  type="checkbox"
                  @change="select()"
                >
                <span class="checkmark" />
                <span class="go-bold label-words">上传时编辑图片</span><i
                  class="iconfont icon-crop-full"
                />
              </label>
            </p>
          </el-row>
          <div style="text-align: center; margin-bottom: 20px;">
            <el-input
              v-model="roiYear"
              style="width: 240px;"
              maxlength="4"
              placeholder="年份（YYYY，用于KML ROI命名）"
              clearable
            />
          </div>
          <el-row
            justify="center"
            align="middle"
          >
            <i
              class="iconfont icon-tuxingtuxiangchuli"
            />
            <p>图像增强：</p>
            <p>
              <label class="prehandle-label container">
                <input
                  ref="clahe"
                  type="checkbox"

                  @change="selectClahe(4)"
                >
                <span class="checkmark" />
                <span class="go-bold label-words">CLAHE</span>
              </label>
            </p>
            <p>
              <label class="prehandle-label container">
                <input
                  ref="sharpen"
                  type="checkbox"

                  @change="selectSharpen(4)"
                >
                <span class="checkmark" />
                <span class="go-bold label-words">锐化</span>
              </label>
            </p>
          </el-row>
          <el-row
            justify="center"
            align="middle"
          >
            <i
              class="iconfont icon-agora_AIjiangzao"
            />
            <p>降噪处理：</p>
            <p>
              <label class="prehandle-label container">
                <input
                  ref="smooth"
                  type="checkbox"

                  @change="selectSmooth()"
                >
                <span class="checkmark" />
                <span class="go-bold label-words">平滑</span>
              </label>
              <label class="prehandle-label container">
                <input
                  ref="filter"
                  type="checkbox"

                  @change="selectFilter()"
                >
                <span class="checkmark" />
                <span class="go-bold label-words">滤波</span>
              </label>
            </p>
          </el-row>
          <div class="handle-button">
            <el-button
              type="primary"
              class="btn-animate btn-animate__shiny"
              @click="upload('地物分类','semantic_segmentation')"
            >
              开始处理
            </el-button>
          </div>
          <el-divider v-if="!uploadSrc.prehandle" />
          <div v-if="uploadSrc.prehandle">
            <div v-if="uploadSrc.prehandle===2">
              <div
                id="sub-title"
              >
                CLAHE处理结果预览<i
                  class="iconfont icon-dianji"
                />
              </div>
            </div>
            <div v-else-if="uploadSrc.prehandle===4">
              <div
                id="sub-title"
              >
                锐化处理结果预览<i
                  class="iconfont icon-dianji"
                />
              </div>
            </div>
            <el-divider />
            <el-row
              justify="center"
              :gutter="20"
            >
              <el-col
                :xs="24"
                :sm="24"
                :md="6"
                :lg="6"
                :xl="6"
              >
                <div
                  v-for="(item,index) in before"
                  :key="index"
                >
                  <el-image
                    :src="item"
                    :preview-src-list="[item]"
                    :preview-teleported="true"
                  />
                  <div class="handle-words">
                    原图
                  </div>
                </div>
              </el-col>
              <el-col
                :md="2"
                :lg="2"
                :xl="2"
              />
              <el-col
                v-if="uploadSrc.prehandle===2"
                :xs="24"
                :sm="24"
                :md="6"
                :lg="6"
                :xl="6"
              >
                <div
                  v-for="(item,index) in claheImg"
                  :key="index"
                >
                  <el-image
                    :src="item"
                    :preview-src-list="[item]"
                    :preview-teleported="true"
                  />
                  <div class="handle-words">
                    CLAHE处理后 <span
                      @click="
                        downloadimgWithWords(
                          -1,
                          item,
                          `CLAHE处理图.png`
                        )
                      "
                    ><i class="iconfont icon-xiazai" /></span>
                  </div>
                </div>
              </el-col>
              <el-col
                v-if="uploadSrc.prehandle===4"
                :xs="24"
                :sm="24"
                :md="6"
                :lg="6"
                :xl="6"
              >
                <div
                  v-for="(item,index) in sharpenImg"
                  :key="index"
                >
                  <el-image
                    :src="item"
                    :preview-src-list="[item]"
                    :preview-teleported="true"
                  />
                  <div class="handle-words">
                    锐化处理后 <span
                      @click="
                        downloadimgWithWords(
                          -1,
                          item,
                          `锐化处理图.png`
                        )
                      "
                    ><i class="iconfont icon-xiazai" /></span>
                  </div>
                </div>
              </el-col>
            </el-row>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <Tabinfor>
      <template #left>
        <div
          id="sub-title"
        >
          结果图预览<i
            class="iconfont icon-dianji"
          />
        </div>
      </template>
    </Tabinfor>
    <el-divider />
    <Tabinfor>
      <template #left>
        <p>
          <span class="go-bold">点击图片</span>即可预览
          <i
            class="iconfont icon-duigou"
          />
          <span><span class="go-bold">滑轮滚动</span>即可放大缩小</span>
        </p>
      </template>
      <template #right>
        <div class="go-bold history-tools">
          <el-button
            size="mini"
            type="danger"
            plain
            style="margin-right: 10px;"
            @click="clearCurrentHistory"
          >
            一键清空历史
          </el-button>
          <i
            class="iconfont icon-shuaxin"
            style="padding-right:65px"
            @click="getMore"
          ><span
            class="hidden-sm-and-down"
          >点击刷新</span></i>
        </div>
      </template>
    </Tabinfor>
    <el-dialog
      v-model="cutVisible"
      :modal="false"
      title="编辑"
      width="75%"
      top="0"
    >
      <MyVueCropper
        :fileimg="fileimg"
        :funtype="funtype"
        :file="file"
        :child-prehandle="uploadSrc.prehandle"
        :child-denoise="uploadSrc.denoise"
        @cut-changed="notvisible"
        @child-refresh="getMore"
      />
    </el-dialog>
    <ImgShow
      :img-arr="imgArr"
      @delete-item="deleteHistoryItem"
    />
    <Bottominfor />
  </div>
</template>

<script>
import { atchDownload, downloadimgWithWords, getImgArrayBuffer } from "@/utils/download.js";
import { imgUpload, createSrc } from "@/api/upload";
import {
  flashHistoryClear,
  flashHistoryDeleteOne,
  historyDeleteOne,
  historyGetPage
} from "@/api/history";
import { getUploadImg, goCompress, upload } from "@/utils/getUploadImg";
import { readProjectId } from "@/utils/interpretationContext.mjs";
import { selectClahe, selectFilter, selectSharpen, selectSmooth, } from "@/utils/preHandle";
import ImgShow from "@/components/ImgShow";
import Tabinfor from "@/components/Tabinfor";
import Bottominfor from "@/components/Bottominfor";
import MyVueCropper from "@/components/MyVueCropper";

export default {
  name: "Segmentation",
  components: {
    ImgShow,
    Tabinfor,
    Bottominfor,
    MyVueCropper,
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      document.querySelector(".el-main").scrollTop = 0;
    });
  },
  data() {
    return {
      isUpload: true,
      canUpload: true,
      claheImg: [],
      sharpenImg: [],
      before: [],
      fileimg: "",
      file: {},
      isNotCut: true,
      cutVisible: false,
      fileList: [],
      funtype: "地物分类",
      scrollTop: "",
      fit: "fill",

      uploadSrc: { list: [], prehandle: 0, denoise: 0 },

      prePhoto: {
        list: [],
        prehandle: 0,
        type: 4
      },
      imgArr:[],
      roiYear: '',
      projectId: readProjectId(window.location.search)
    };
  },
  created() {
    this.getUploadImg("地物分类");
  },
  beforeUnmount() {
    // 离开页面时释放预览 object URL，避免内存泄漏
    if (this.fileimg) {
      window.URL.revokeObjectURL(this.fileimg);
      this.fileimg = "";
    }
  },
  methods: {
    getImgArrayBuffer,
    atchDownload,
    imgUpload,
    historyGetPage,
    historyDeleteOne,
    createSrc,
    getUploadImg,
    upload,
    goCompress,
    selectSharpen,
    selectFilter,
    selectSmooth,
    selectClahe,
    downloadimgWithWords,
    flashHistoryDeleteOne,
    flashHistoryClear,
    clearQueue() {
      this.fileList = [];
      if (this.$refs.folderInput) this.$refs.folderInput.value = "";
      if (this.$refs.fileInput) this.$refs.fileInput.value = "";
      this.$message.success("清除成功");
    },
    notvisible() {
      this.cutVisible = false;
      this.fileList = [];
      if (this.$refs.folderInput) this.$refs.folderInput.value = "";
      if (this.$refs.fileInput) this.$refs.fileInput.value = "";
    },
    getMore() {
      this.getUploadImg("地物分类");
    },
    deleteHistoryItem(item) {
      if (item.record_source === 'project') {
        this.$message.warning("项目同步结果请在对应矿山项目中管理");
        return;
      }
      this.$confirm("删除该条历史？", "提示", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      }).then(() => {
        if (item.record_source === 'standalone') {
          return this.historyDeleteOne(item.id);
        }
        return this.flashHistoryDeleteOne(item.record_id);
      }).then(() => {
        this.$message.success("删除成功");
        this.getMore();
      }).catch(() => {});
    },
    clearCurrentHistory() {
      this.$confirm("确认清空全部历史？", "提示", {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        type: "warning",
      }).then(() => {
        return this.flashHistoryClear();
      }).then(() => {
        this.$message.success("清理完成");
        this.getMore();
      }).catch(() => {});
    },
    fileClick() {
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
    isValidTiff(fileLike) {
      const raw = fileLike?.raw || fileLike?.file || fileLike;
      const name = String(fileLike?.relativePath || raw?.webkitRelativePath || raw?.name || "");
      const fileSuffix = name.substring(name.lastIndexOf(".") + 1);
      return ["tif", "tiff", "TIF", "TIFF"].includes(fileSuffix);
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
      }});
    },
    setPreviewFile(fileLike) {
      const file = fileLike?.raw || fileLike?.file || fileLike;
      this.cutVisible = !!this.$refs.cut?.checked;
      this.canUpload = true;
      // MyVueCropper 依赖 file.name 生成裁剪产物文件名，缺失会得到 undefined.png
      this.file = file;
      // 释放上一张预览的 object URL，避免反复选图累积泄漏
      if (this.fileimg) {
        window.URL.revokeObjectURL(this.fileimg);
      }
      this.fileimg = window.URL.createObjectURL(file);
    },
    disableCutForBatchUpload() {
      if (this.$refs.cut?.checked) {
        this.$refs.cut.checked = false;
        this.cutVisible = false;
        this.isNotCut = true;
        this.$message.warning("整文件夹/批量上传不支持上传时编辑，已自动关闭编辑模式");
      }
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
        this.cutVisible = false;
        this.canUpload = false;
        this.$message.error("只允许上传 tif / tiff 格式,请重新上传");
        return;
      }
      if (validFiles.length > 1) {
        this.disableCutForBatchUpload();
      }
      this.fileList = this.createUploadItems(validFiles);
      this.setPreviewFile(validFiles[validFiles.length - 1]);
    },
    handleUploadChange(file, uploadFiles) {
      const rawFiles = (uploadFiles || [])
        .map((item) => item?.raw || item)
        .filter(Boolean);
      this.replaceFileList(rawFiles);
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
      this.disableCutForBatchUpload();
      this.replaceFileList(rawFiles);
      event.target.value = "";
    },
    async handleNativeDrop(event) {
      const items = Array.from(event?.dataTransfer?.items || []);
      const filesFromDrop = await this.readDroppedItems(items);
      if (filesFromDrop.length > 0) {
        this.disableCutForBatchUpload();
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
        .map((item) => item.webkitGetAsEntry ? item.webkitGetAsEntry() : null)
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
    select() {
      this.isNotCut = this.$refs.cut.checked;
    },
  },
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
  left: 0;
  right: 0;
  width: 220px;
}

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

.el-radio {
  height: auto !important;
  margin-bottom: 8px;
  margin-right: 20px;
  display: inline-flex;
  align-items: center;
}

.el-radio /deep/ .el-radio__label {
  display: flex;
  align-items: center;
  padding-left: 10px;
}

.el-radio /deep/ .el-radio__input {
  margin-top: 0;
}

</style>
