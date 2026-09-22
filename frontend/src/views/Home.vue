<template>
  <el-container>
    <el-aside width="auto">
      <AsideVue
        :is-collapse="isCollapse"
        :active-index="activeIndex"
      />
    </el-aside>
    <el-container>
      <el-main
        class="main-ctx"
      >
        <el-header class="platform-header">
          <el-row align="middle" justify="space-between">
            <el-col :span="2">
              <i
                class="icon-menu"
                @click="goCollapse"
              />
            </el-col>
            <el-col :span="22">
              <Tablogin />
            </el-col>
          </el-row>
        </el-header>
        <router-view v-slot="{ Component }">
          <transition
            name="fade"
            mode="out-in"
          >
            <component :is="Component" />
          </transition>
        </router-view>
        <el-backtop
          target=".main-ctx"
          :bottom="40"
          :visibility-height="50"
          :right="27"
        />
      </el-main>
    </el-container>
  </el-container>
</template>

<script>
import "@/assets/css/app.css";
import AsideVue from "@/components/AsideVue";
import Tablogin from "@/components/Tablogin";

export default {
  name: "Home",
  components: {
    AsideVue,
    Tablogin,
  },
  data() {
    return {
      isCollapse: false,
      scrollTop: "",
      activeIndex: this.$route.path,
    };
  },
  mounted() {
    // 响应式折叠：addEventListener 便于卸载时移除（原先覆盖 window.onresize 且卸载后仍持有已销毁实例）
    this.handleResize = () => {
      this.isCollapse = document.documentElement.clientWidth <= 1100;
    };
    this.handleResize();
    window.addEventListener("resize", this.handleResize);
    document.body.style.overflow = "hidden";
  },
  beforeUnmount() {
    window.removeEventListener("resize", this.handleResize);
    // 离开应用壳（登录/404 页）时恢复滚动，原先一直锁死 body
    document.body.style.overflow = "";
  },
  updated(){
    this.activeIndex=this.$route.path
  },
  methods: {
    goCollapse() {
      this.isCollapse = !this.isCollapse;
    },
  }
};
</script>

<style scoped>
.el-main {
  --el-main-padding: 0px 20px 0 20px;
  height: auto;
  width: 100%;
  overflow-x: hidden;
}
.main-ctx {
  height: 100vh;
}
.fade-enter-active,
.fade-leave-active {
  transition: all 0.25s 
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.platform-header {
  padding: 0 20px;
  height: 60px;
  line-height: normal;
  background-color: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
}

.platform-header .el-row {
  width: 100%;
  min-height: 60px;
  align-items: center;
}

.platform-header :deep(.el-col) {
  display: flex;
  align-items: center;
}

.platform-header :deep(.el-col:nth-child(2)) {
  min-width: 0;
}

.platform-header .icon-menu {
  font-size: 20px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: color var(--transition-fast);
}

.platform-header .icon-menu:hover {
  color: var(--primary-color);
}
</style>
