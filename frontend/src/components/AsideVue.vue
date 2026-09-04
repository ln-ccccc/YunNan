<template>
  <el-menu
    class="el-menu-vertical-demo geoview-sidebar"
    :collapse="isCollapse"
    :default-active="activeIndex"
  >
    <div class="platform">
      <div
        v-if="!isCollapse"
        id="platform-title"
      >
        <a
          class="platform-title"
          @click="goShow"
        >智能遥感解译系统</a>
      </div>
    </div>
    <el-divider content-position="center">
      <span
        v-show="!isCollapse"
        class="divider-title"
      >分析工具</span>
    </el-divider>

    <el-menu-item
      index="/segmentation"
      @click="goSegmentation"
    >
      <i
        v-show="isCollapse"
        class="iconfont icon-erfenleibianhuajiance16px"
      />
      <h3 v-show="!isCollapse">
        <i class="iconfont icon-erfenleibianhuajiance16px" />地表覆盖分类
      </h3>
    </el-menu-item>

    <el-menu-item
      index="/spectralindices"
      @click="goSpectralIndices"
    >
      <i
        v-show="isCollapse"
        class="iconfont icon-jishu"
      />
      <h3 v-show="!isCollapse">
        <i class="iconfont icon-jishu" />光谱指数计算
      </h3>
    </el-menu-item>

    <el-divider content-position="center">
      <span
        v-show="!isCollapse"
        class="divider-title"
      />
    </el-divider>

    <div v-if="!isCollapse" class="sidebar-panels">
      <div class="glass-card">
        <div class="card-title">快捷入口</div>
        <div class="quick-row">
          <div class="quick-item" @click="goSegmentation">地表覆盖分类</div>
          <div class="quick-item" @click="goSpectralIndices">光谱指数计算</div>
        </div>
      </div>
      <div class="glass-card">
        <div class="card-title">操作提示</div>
        <div class="tips">
          <div class="tip-line">地物分类：上传 tif/tiff + 填写年份（YYYY），结果按矿区FID落盘展示</div>
          <div class="tip-line">矿区边界：可在 Miner 侧上传/更新 KML，无需手工挂载</div>
          <div class="tip-line">光谱指数：历史“原图”显示为彩色预览，便于对照</div>
        </div>
      </div>
    </div>
  </el-menu>
</template>

<script>
import {
  goSegmentation,
  goSpectralIndices
} from "@/utils/gosomewhere.js";

export default {
  props: {
    isCollapse: {
      type: Boolean,
      default: false
    },
    activeIndex: {
      type: String,
      default: "/segmentation"
    }
  },
  methods: {
    goSegmentation,
    goSpectralIndices,
    goShow() {
      this.$message.success("欢迎使用智能遥感解译系统");
    }
  }
};
</script>

<style lang="less">
.el-menu {
  position: relative;
  height: 100vh;
  top: 0;
  bottom: 0;
  text-align: center;
  font-family: Microsoft JhengHei UI, sans-serif;
  display: flex;
  flex-direction: column;

  .el-menu-item {
    padding: 0 0;
    border-radius: 10px;
    position: relative;
    color: var(--text-secondary);
    z-index: 1;
    h3 {
      padding-right: 30px;
      width: 100%;
      margin: 0 auto;
      .iconfont {
        font-weight: normal;
        margin-right: 5px;
      }
    }
  }
  .el-menu-item:hover {
    background-color: rgba(78, 205, 196, 0.12) !important;
    color: var(--text-primary) !important;
  }

  .el-menu-item :hover::after {
    width: 100%;
    background: rgba(78, 205, 196, 0.25);
  }
  .el-menu-item ::after {
    position: absolute;
    content: "";
    width: 0;
    height: 100%;
    top: 0;
    left: 0;
    border-radius: 10px;
    transition: 0.25s;
    z-index: -1;
  }
}
.el-menu-vertical-demo:not(.el-menu--collapse) {
  width: 350px;
  min-height: 400px;
}

.is-active {
  background-color: rgba(78, 205, 196, 0.18);
  border: 1px solid rgba(78, 205, 196, 0.35);
  h3,
  i {
    color: var(--text-primary) !important;
  }
}

.platform {
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  padding: 0 20px;
  text-align: center;
  color: var(--primary-color);
  height: 64px;
  width: auto;
  overflow: hidden;

  .platform-title {
    color: var(--text-primary);
    font-family: Microsoft JhengHei UI, sans-serif;
    font-size: 16px;
    line-height: 20px;
    margin-top: 0;
  }
}

.divider-title {
  display: block;
  line-height: 24.4px;
  overflow: hidden;
  width: 70px;
  color: var(--text-muted);
}

#platform-title {
  position: relative;
  font-size: 18px;
  font-weight: 1000;
  cursor: pointer;
}

#platform-title::after {
  content: "";
  width: 0;
  height: 3px;
  background: rgba(78, 205, 196, 0.85);
  position: absolute;
  top: 100%;
  left: 50%;
  right: 50%;
  transition: all 0.5s;
}

#platform-title:hover:after {
  left: 7%;
  right: 7%;
  width: 85%;
}
.el-menu .el-divider__text {
  background-color: transparent;
}

.geoview-sidebar {
  padding: 6px 10px 14px 10px;
}

.sidebar-panels {
  margin-top: auto;
  padding: 10px 8px 14px 8px;
  display: grid;
  gap: 12px;
}

.glass-card {
  text-align: left;
  padding: 12px 12px;
  border-radius: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  backdrop-filter: blur(14px);
  box-shadow: var(--shadow-md);
}

.card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.4px;
  margin-bottom: 10px;
}

.quick-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.quick-item {
  padding: 10px 10px;
  border-radius: 10px;
  background: var(--bg-hover);
  border: 1px solid var(--border-color);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
  transition: transform 0.15s ease, border-color 0.15s ease;
}

.quick-item:hover {
  transform: translateY(-1px);
  border-color: var(--border-dark);
}

.tips {
  display: grid;
  gap: 8px;
}

.tip-line {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}
</style>
