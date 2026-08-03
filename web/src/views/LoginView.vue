<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type AuthProvider } from '@/api/client'
import { useAuth } from '@/composables/useAuth'

const { loginWithAccount, loginWithDingTalkMock } = useAuth()
const router = useRouter()
const route = useRoute()

const username = ref('alice')
const password = ref('demo123')
const loading = ref(false)
const error = ref('')
const providers = ref<AuthProvider[]>([])
const providersLoaded = ref(false)
const demoPasswordHint = ref('demo123')

// 默认允许账号登录；providers 拉取失败时也不要藏掉表单
const accountEnabled = computed(() => {
  if (!providersLoaded.value || providers.value.length === 0) return true
  return providers.value.some((p) => p.id === 'account' && p.enabled)
})
const optionalProviders = computed(() =>
  providers.value.filter((p) => p.id !== 'account'),
)

const demos = [
  { username: 'alice', label: 'alice · 普通员工' },
  { username: 'delivery_manager', label: 'delivery_manager · 交付主管（可批本部门）' },
  { username: 'finance01', label: 'finance01 · 财务主管' },
  { username: 'boss', label: 'boss · 老板（可批全部/机密）' },
  { username: 'legal01', label: 'legal01 · 法务主管' },
  { username: 'admin', label: 'admin · 管理员' },
]

onMounted(async () => {
  try {
    const res = await api.authProviders()
    providers.value = res.providers
    if (res.demo_password_hint) {
      demoPasswordHint.value = res.demo_password_hint
      password.value = res.demo_password_hint
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法连接后端，请确认服务已启动'
  } finally {
    providersLoaded.value = true
  }
})

async function afterLogin() {
  router.replace((route.query.redirect as string) || '/')
}

async function submitAccount() {
  loading.value = true
  error.value = ''
  try {
    await loginWithAccount(username.value.trim(), password.value)
    await afterLogin()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loading.value = false
  }
}

async function mockDingTalk() {
  loading.value = true
  error.value = ''
  try {
    await loginWithDingTalkMock('ding_alice')
    await afterLogin()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loading.value = false
  }
}

function goOAuth(provider: AuthProvider) {
  if (provider.login_url) {
    window.location.href = provider.login_url
    return
  }
  error.value = `${provider.name}尚未配置，请先在环境变量中开启并填写密钥`
}
</script>

<template>
  <div class="login-page">
    <section class="panel panel-pad stack card">
      <div>
        <h1 class="page-title">企业智能体</h1>
        <p class="page-sub">上传公司资料，对话查询制度。登录方式可按公司通讯工具配置，不绑定单一平台。</p>
      </div>

      <form v-if="accountEnabled" class="stack" @submit.prevent="submitAccount">
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
          {{ loading ? '登录中…' : '账号登录' }}
        </button>
        <p class="muted tip">演示账号密码统一为：{{ demoPasswordHint }}</p>
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

      <div class="divider"><span>可选企业通讯登录</span></div>

      <div class="stack">
        <button
          v-for="p in optionalProviders"
          :key="p.id"
          class="btn btn-ghost btn-block provider"
          type="button"
          :disabled="loading || (!p.enabled && !(p.id === 'dingtalk' && p.mock_enabled))"
          @click="p.enabled && p.login_url ? goOAuth(p) : p.id === 'dingtalk' && p.mock_enabled ? mockDingTalk() : goOAuth(p)"
        >
          {{ p.name }}{{ p.enabled ? '' : '（未配置）' }}
        </button>
        <p class="muted tip">不使用钉钉/企微/飞书也没关系，默认用上面的账号登录即可。</p>
      </div>

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

.divider {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 10px;
  align-items: center;
  color: var(--muted);
  font-size: 0.82rem;
}

.divider::before,
.divider::after {
  content: '';
  height: 1px;
  background: var(--line);
}

.provider:disabled {
  opacity: 0.55;
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
