<template>
  <div class="settings-view">
    <header class="view-header">
      <div>
        <p class="kicker">系统设置</p>
        <h1>平台信息</h1>
      </div>
    </header>

    <section class="view-body">
      <div class="info-grid">
        <article class="info-card">
          <span>平台</span>
          <strong>矿山生态修复智能监测平台</strong>
        </article>
        <article class="info-card">
          <span>主控台版本</span>
          <strong>M1–M4（2026-09-22）</strong>
        </article>
        <article class="info-card">
          <span>当前用户</span>
          <strong>{{ username || 'admin' }}</strong>
        </article>
        <article class="info-card">
          <span>项目总数</span>
          <strong>{{ stats?.project_total ?? '—' }}</strong>
        </article>
      </div>

      <h3>功能模块状态</h3>
      <ul class="module-list">
        <li v-for="item in modules" :key="item.key">
          <span>{{ item.label }}</span>
          <span :class="item.implemented ? 'ok' : 'pending'">
            {{ item.implemented ? '已上线' : '规划中（' + item.milestone + '）' }}
          </span>
        </li>
      </ul>

      <h3>口令修改</h3>
      <form class="password-form" @submit.prevent="submitPasswordChange">
        <div class="pwd-row">
          <label for="pwd-old">原口令</label>
          <input id="pwd-old" v-model="pwdForm.oldPassword" type="password" autocomplete="current-password" required />
        </div>
        <div class="pwd-row">
          <label for="pwd-new">新口令</label>
          <input id="pwd-new" v-model="pwdForm.newPassword" type="password" autocomplete="new-password" minlength="8" required />
        </div>
        <div class="pwd-row">
          <label for="pwd-confirm">确认新口令</label>
          <input id="pwd-confirm" v-model="pwdForm.confirm" type="password" autocomplete="new-password" minlength="8" required />
        </div>
        <p v-if="pwdMessage.text" :class="pwdMessage.ok ? 'ok-text' : 'error-text'">{{ pwdMessage.text }}</p>
        <button class="primary-btn" type="submit" :disabled="pwdSubmitting">更新口令</button>
      </form>

      <p class="muted-text note">
        多用户权限属后期范围（本期不做 RBAC）；口令修改后请妥善保管新口令——服务重启会以服务器 .env 中
        ADMIN_PASSWORD 覆盖，改密后需管理员同步更新服务器配置。
      </p>
    </section>
  </div>
</template>

<script setup>
import axios from 'axios';
import { onMounted, ref } from 'vue';

defineProps({ username: { type: String, default: '' } });

const MINER_API_BASE_URL = import.meta.env.VITE_MINER_API_BASE_URL || '';

const stats = ref(null);

const pwdForm = ref({ oldPassword: '', newPassword: '', confirm: '' });
const pwdSubmitting = ref(false);
const pwdMessage = ref({ text: '', ok: false });

const submitPasswordChange = async () => {
  pwdMessage.value = { text: '', ok: false };
  if (pwdForm.value.newPassword !== pwdForm.value.confirm) {
    pwdMessage.value = { text: '两次输入的新口令不一致', ok: false };
    return;
  }
  if (pwdForm.value.newPassword.length < 8) {
    pwdMessage.value = { text: '新口令至少 8 位', ok: false };
    return;
  }
  pwdSubmitting.value = true;
  try {
    const res = await axios.post(
      `${MINER_API_BASE_URL}/api/auth/change-password`,
      { old_password: pwdForm.value.oldPassword, new_password: pwdForm.value.newPassword },
    );
    pwdMessage.value = { text: res.data?.msg || '口令修改成功', ok: true };
    pwdForm.value = { oldPassword: '', newPassword: '', confirm: '' };
  } catch (e) {
    pwdMessage.value = { text: e?.response?.data?.msg || '口令修改失败，请稍后重试', ok: false };
  } finally {
    pwdSubmitting.value = false;
  }
};

const modules = [
  { key: 'projects', label: '项目管理', implemented: true },
  { key: 'imagery', label: '影像管理', implemented: true },
  { key: 'interpretation', label: '智能解译', implemented: true, milestone: '发起经地图工作台' },
  { key: 'editing', label: '图斑编辑', implemented: true },
  { key: 'data', label: '数据管理', implemented: true, milestone: '' },
  { key: 'search', label: '查询搜索', implemented: true },
  { key: 'settings', label: '系统设置', implemented: true },
];

onMounted(async () => {
  try {
    const res = await axios.get(`${MINER_API_BASE_URL}/api/stats/overview`);
    stats.value = res.data?.data || null;
  } catch (_) {
    stats.value = null;
  }
});
</script>

<style scoped>
.settings-view { max-width: 720px; margin: 0 auto; padding: 28px 24px; color: #264b45; }
.view-header { margin-bottom: 18px; }
.view-header h1 { margin: 4px 0 6px; font-size: 22px; }
.kicker { color: #5a7d75; font-size: 13px; margin: 0; }

.view-body {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(38, 75, 69, 0.15);
  border-radius: 10px;
  padding: 18px 20px;
}

.info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 14px; }
.info-card {
  background: rgba(38, 75, 69, 0.05); border: 1px solid rgba(38, 75, 69, 0.12);
  border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px;
}
.info-card span { font-size: 12px; color: #5a7d75; }
.info-card strong { font-size: 15px; }

h3 { font-size: 14px; color: #2f6f61; margin: 14px 0 8px; }

.module-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.module-list li {
  display: flex; justify-content: space-between; font-size: 13px;
  background: rgba(38, 75, 69, 0.04); border-radius: 6px; padding: 8px 12px;
}
.ok { color: #00a383; }
.pending { color: #b8860b; }

.muted-text { color: #5a7d75; font-size: 12px; }
.password-form { display: grid; gap: 10px; margin-top: 6px; max-width: 420px; }
.pwd-row { display: grid; gap: 4px; }
.pwd-row label { font-size: 13px; color: #264b45; }
.pwd-row input {
  padding: 8px 10px; border-radius: 6px; border: 1px solid rgba(38, 75, 69, 0.3); font-size: 13px;
}
.primary-btn {
  background: #2f6f61; color: #fff; border: none; border-radius: 6px;
  padding: 8px 18px; cursor: pointer; font-size: 13px; justify-self: start;
}
.primary-btn:disabled { cursor: not-allowed; opacity: 0.55; }
.ok-text { color: #2f6f61; font-size: 13px; }
.note { margin-top: 14px; }
</style>
