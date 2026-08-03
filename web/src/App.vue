<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { useAuth } from '@/composables/useAuth'

const { ready, isLoggedIn, bootstrap } = useAuth()
const route = useRoute()
const router = useRouter()

onMounted(async () => {
  await bootstrap()
})

watch([ready, isLoggedIn, () => route.fullPath], ([isReady, loggedIn]) => {
  if (!isReady) return
  const isPublic = route.meta.public === true
  if (!loggedIn && !isPublic) {
    router.replace({ name: 'login', query: { redirect: route.fullPath } })
  }
  if (loggedIn && route.name === 'login') {
    router.replace((route.query.redirect as string) || '/')
  }
})
</script>

<template>
  <div v-if="!ready" class="boot">加载中…</div>
  <RouterView v-else-if="route.meta.public" />
  <AppShell v-else-if="isLoggedIn">
    <RouterView />
  </AppShell>
</template>

<style scoped>
.boot {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  color: var(--muted);
}
</style>
