<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

const { loginWithAccount } = useAuth()
const router = useRouter()
const route = useRoute()

const username = ref('alice')
const password = ref('demo123')
const loading = ref(false)
const error = ref('')

const demos = [
  { username: 'alice', label: 'alice · 普通员工' },
  { username: 'delivery_manager', label: 'delivery_manager · 交付主管' },
  { username: 'finance01', label: 'finance01 · 财务' },
  { username: 'boss', label: 'boss · 老板' },
  { username: 'legal01', label: 'legal01 · 法务' },
  { username: 'admin', label: 'admin · 管理员' },
]

async function submitAccount() {
  loading.value = true
  error.value = ''
  try {
    await loginWithAccount(username.value.trim(), password.value)
    router.replace((route.query.redirect as string) || '/')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="panel panel-pad stack card">
      <div>
        <h1 class="page-title">企业智能体</h1>
        <p class="page-sub">上传资料进知识库，对话查询与办事。当前：账号密码登录。</p>
      </div>

      <form class="stack" @submit.prevent="submitAccount">
        <div class="field">
          <label for="username">公司账号</label>
          <input id="username" v-model="username" autocomplete="username" placeholder="输入用户名" />
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="输入密码"
          />
        </div>
        <button class="btn btn-primary btn-block" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
        <p class="muted tip">演示密码统一：demo123</p>
        <div class="demos">
          <button
            v-for="item in demos"
            :key="item.username"
            class="demo"
            type="button"
            :disabled="loading"
            @click="username = item.username"
          >
            {{ item.label }}
          </button>
        </div>
      </form>

      <p v-if="error" class="error">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 24px 16px;
}

.card {
  width: min(440px, 100%);
}

.demos {
  display: grid;
  gap: 8px;
}

.demo {
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #f7fafb;
  text-align: left;
  padding: 0 12px;
  font-size: 0.9rem;
}

.tip {
  margin: 0;
  font-size: 0.84rem;
}

.error {
  margin: 0;
  color: var(--danger);
}
</style>
