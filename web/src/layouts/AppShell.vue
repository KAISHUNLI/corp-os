<script setup lang="ts">
import { computed } from 'vue'
import { useAuth } from '@/composables/useAuth'

const { user, logout } = useAuth()
const subtitle = computed(() => `${user.value?.display_name || ''} · ${user.value?.department_code || ''}`)
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="mark" aria-hidden="true" />
        <div>
          <strong>corp-os</strong>
          <p>企业智能体</p>
        </div>
      </div>
      <div class="userbox">
        <span class="who">{{ subtitle }}</span>
        <button class="btn btn-ghost logout" type="button" @click="logout">退出</button>
      </div>
    </header>
    <main class="main">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.shell {
  height: 100dvh;
  max-height: 100dvh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.topbar {
  flex: 0 0 auto;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.9);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand strong {
  display: block;
  font-size: 1rem;
}

.brand p {
  margin: 0;
  color: var(--muted);
  font-size: 0.78rem;
}

.mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background:
    linear-gradient(145deg, #163445, #0b1f2a 60%),
    radial-gradient(circle at 70% 30%, #2dd4bf 0%, transparent 45%);
}

.userbox {
  display: flex;
  align-items: center;
  gap: 8px;
}

.who {
  color: var(--muted);
  font-size: 0.8rem;
  max-width: 42vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout {
  min-height: 34px;
  padding: 0 10px;
  font-size: 0.8rem;
}

.main {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.main > :deep(*) {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
</style>
