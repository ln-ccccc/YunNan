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
        <TiffUploadCard
          v-model="fileList"
          clear-text="清空图片"
          @select="onFilesSelected"
        >

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
        </TiffUploadCard>
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
import TiffUploadCard from "@/components/TiffUploadCard.vue";

export default {
  name: "Segmentation",
  components: {
    ImgShow,
    Tabinfor,
    Bottominfor,
    MyVueCropper,
    TiffUploadCard,
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
    notvisible() {
      this.cutVisible = false;
      this.fileList = [];
    },
    // TiffUploadCard select 联动：空=全部被拒（收起裁剪弹层）；多文件=批量，自动关裁剪；末张进裁剪预览
    onFilesSelected(items) {
      if (items.length === 0) {
        this.cutVisible = false;
        this.canUpload = false;
        return;
      }
      if (items.length > 1) {
        this.disableCutForBatchUpload();
      }
      this.setPreviewFile(items[items.length - 1]);
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
</style>
