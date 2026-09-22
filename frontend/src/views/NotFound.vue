<template>
  <div class="notfound">
    <div class="notfound__code">
      4<span class="notfound__zero">0</span>4
    </div>
    <div class="notfound__subtitle">
      哎呀。看来你拐错弯了。
    </div>
    <div class="notfound__actions">
      <button
        type="button"
        @click="goHome"
      >
        返回首页
      </button>
      <button
        type="button"
        @click="goBack"
      >
        返回上一页
      </button>
    </div>
  </div>
</template>

<script>
export default {
  name: "Notfound",
  methods: {
    goBack() {
      this.$router.go(-1);
    },
    goHome() {
      // 回真实存在的首页路由（detectchanges 从未注册，点击后仍停留 404）
      this.$router.push('/home');
    },
  },
};
</script>

<style scoped>
.notfound {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24px;
  font-family: "Microsoft Yahei", sans-serif;
}

.notfound__code {
  font-size: 96px;
  font-weight: 700;
  letter-spacing: 8px;
  color: var(--el-color-primary, #409eff);
}

.notfound__zero {
  display: inline-block;
  transform: translateY(-8px);
  color: var(--el-color-warning, #e6a23c);
}

.notfound__subtitle {
  font-size: 18px;
  color: var(--el-text-color-secondary, #909399);
}

.notfound__actions button {
  margin: 0 12px;
  padding: 8px 22px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: #fff;
  background: var(--el-color-primary, #409eff);
  font-size: 14px;
}

.notfound__actions button:hover {
  opacity: 0.85;
}
</style>
