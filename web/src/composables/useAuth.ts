import { computed, ref } from 'vue'
import type { UserInfo } from '@/api/client'
import { api, clearAuthStorage, persistAuth, getStoredToken } from '@/api/client'

const user = ref<UserInfo | null>(null)
const ready = ref(false)

export function useAuth() {
  const isLoggedIn = computed(() => !!user.value)

  async function bootstrap() {
    const token = getStoredToken()
    if (!token) {
      clearAuthStorage()
      user.value = null
      ready.value = true
      return
    }
    try {
      user.value = await api.me()
    } catch {
      clearAuthStorage()
      user.value = null
    } finally {
      ready.value = true
    }
  }

  async function loginWithAccount(username: string, password: string) {
    const info = await api.login(username, password)
    persistAuth(info)
    user.value = info
  }

  function logout() {
    clearAuthStorage()
    user.value = null
  }

  return {
    user,
    ready,
    isLoggedIn,
    bootstrap,
    loginWithAccount,
    logout,
  }
}
