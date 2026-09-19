<template>
  <div class="login-page">
    <div class="login-card">
      <p class="login-kicker">矿山项目化监测平台</p>
      <h1>管理员登录</h1>
      <p class="login-subtitle">输入管理员账号和密码后，才能访问项目工作台与矿山地图。</p>
      <form class="login-form" @submit.prevent="submitLogin">
        <label>
          <span>账号</span>
          <input v-model.trim="username" type="text" autocomplete="username" required />
        </label>
        <label>
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <button class="login-btn" type="submit" :disabled="submitting">
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>
      <p v-if="error" class="error-text">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';

const props = defineProps({
  submitting: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
});

const emit = defineEmits(['login']);
// 不预填真实账号名：公开登录页直接暴露有效账号会降低暴力破解成本
const username = ref('');
const password = ref('');

const submitLogin = () => {
  if (props.submitting) {
    return;
  }
  emit('login', { username: username.value, password: password.value });
};
</script>

<style scoped>
.login-page {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(circle at top left, rgba(8, 132, 227, 0.15), transparent 28%),
    radial-gradient(circle at bottom right, rgba(46, 204, 113, 0.14), transparent 26%),
    linear-gradient(180deg, #eef5f3 0%, #dce8e3 100%);
  padding: 24px;
  box-sizing: border-box;
}

.login-card {
  width: min(100%, 420px);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(27, 84, 74, 0.12);
  border-radius: 22px;
  padding: 28px;
  box-shadow: 0 24px 48px rgba(20, 54, 48, 0.14);
  color: #163030;
}

.login-kicker,
.login-subtitle,
.error-text,
label span {
  margin: 0;
}

.login-kicker {
  color: #2f7a68;
  font-size: 13px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.login-card h1 {
  margin: 10px 0 8px;
  font-size: 34px;
}

.login-subtitle {
  color: #5d6f6d;
  line-height: 1.6;
}

.login-form {
  display: grid;
  gap: 14px;
  margin-top: 22px;
}

.login-form label {
  display: grid;
  gap: 8px;
}

.login-form input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid rgba(35, 86, 78, 0.15);
  border-radius: 12px;
  padding: 12px 14px;
  background: #fff;
  color: #163030;
  font: inherit;
}

.login-btn {
  border: none;
  border-radius: 12px;
  padding: 12px 14px;
  background: #2f7a68;
  color: #fff;
  font: inherit;
  cursor: pointer;
}

.login-btn:disabled {
  cursor: wait;
  opacity: 0.72;
}

.error-text {
  margin-top: 14px;
  color: #b43c2f;
}
</style>
