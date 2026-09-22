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
        <TiffUploadCard
          v-model="fileList"
          clear-text="清空影像"
        >
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
        </TiffUploadCard>
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
import TiffUploadCard from "@/components/TiffUploadCard.vue";

export default {
  name: "SpectralIndices",
  components: {
    Tabinfor,
    Bottominfor,
    ImgShow,
    TiffUploadCard
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

.option-row {
  margin-top: 25px;
}
</style>
