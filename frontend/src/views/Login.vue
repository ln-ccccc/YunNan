<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-title">管理员登录</div>
      <div class="login-subtitle">登录后才能访问解译平台、历史记录和上传结果。</div>
      <div v-if="sessionNotice" class="session-notice" role="status">
        {{ sessionNotice }}
      </div>
      <el-form @submit.prevent="submitLogin">
        <el-form-item label="账号">
          <el-input v-model="form.username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            autocomplete="current-password"
            show-password
            type="password"
            @keyup.enter="submitLogin"
          />
        </el-form-item>
        <div v-if="errorMessage" class="error-text">{{ errorMessage }}</div>
        <el-button :loading="loading" class="submit-btn" type="primary" @click="submitLogin">
          登录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script>
import { ElMessage } from "element-plus";

import { legacyLogin } from "@/api/auth";

export default {
  name: "LegacyLogin",
  data() {
    return {
      loading: false,
      errorMessage: "",
      sessionNotice: "",
      form: {
        username: "admin",
        password: "",
      },
    };
  },
  created() {
    // 会话过期跳转携带 reason（/login?reason=expired&redirect=...，见
    // utils/authRedirect.js 的 redirectToLegacyLogin），这里是它的唯一消费者：
    // 给出明确文案，不让用户面对无解释的登录页。
    const reason = String(this.$route?.query?.reason || "").trim();
    if (reason === "expired") {
      this.sessionNotice = "登录已过期，请重新登录";
    } else if (reason) {
      this.sessionNotice = "登录状态已失效，请重新登录";
    }
  },
  methods: {
    async submitLogin() {
      if (this.loading) return;
      if (!this.form.username.trim() || !this.form.password) {
        this.errorMessage = "请输入账号和密码";
        return;
      }

      this.loading = true;
      this.errorMessage = "";
      try {
        await legacyLogin({
          username: this.form.username.trim(),
          password: this.form.password,
        });
        ElMessage.success("登录成功");
        this.$router.replace(this.$route.query.redirect || "/segmentation");
      } catch (error) {
        this.errorMessage = error?.response?.data?.msg || error?.message || "登录失败";
      } finally {
        this.loading = false;
      }
    },
  },
};
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: linear-gradient(135deg, #eef5f2 0%, #dde9e3 45%, #f3f8f6 100%);
}

.login-card {
  width: min(420px, 100%);
  padding: 32px 28px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(33, 88, 74, 0.12);
  border-radius: 18px;
  box-shadow: 0 18px 45px rgba(38, 61, 54, 0.12);
}

.login-title {
  font-size: 28px;
  font-weight: 700;
  color: #16352f;
}

.login-subtitle {
  margin: 10px 0 24px;
  color: #59736b;
  line-height: 1.6;
}

.session-notice {
  margin: -10px 0 18px;
  padding: 9px 10px;
  border-left: 2px solid #e6a23c;
  background: rgba(230, 162, 60, 0.08);
  color: #a16207;
  font-size: 13px;
  line-height: 1.5;
  border-radius: 4px;
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
}

.error-text {
  margin-bottom: 10px;
  color: #d94c4c;
  font-size: 13px;
}
</style>
