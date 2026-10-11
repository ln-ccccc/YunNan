<template>
  <div class="interpretation-page">
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
        >

          <el-row justify="center">
            <p>
              地理栅格按原文件上传，保留原始波段和坐标；如需地理裁剪，请前往项目工作台的影像管理。
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
import TiffUploadCard from "@/components/TiffUploadCard.vue";

export default {
  name: "Segmentation",
  components: {
    ImgShow,
    Tabinfor,
    Bottominfor,
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
      claheImg: [],
      sharpenImg: [],
      before: [],
      fileList: [],
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
