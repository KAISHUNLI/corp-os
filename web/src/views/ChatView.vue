<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref } from 'vue'
import '@vue-office/docx/lib/index.css'
import { api, isAbortError, type ChatSession, type Citation } from '@/api/client'

const VueOfficePptx = defineAsyncComponent(() => import('@vue-office/pptx'))
const VueOfficeDocx = defineAsyncComponent(() => import('@vue-office/docx'))

interface UiMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  citations?: Citation[]
}

const sessionId = ref<number | null>(null)
const sessions = ref<ChatSession[]>([])
const input = ref('')
const sending = ref(false)
const uploading = ref(false)
const loadingSession = ref(false)
const deleting = ref(false)
const pendingDelete = ref<ChatSession | null>(null)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const chatAbort = ref<AbortController | null>(null)
const chatGen = ref(0)

const welcomeMessage: UiMessage = {
  id: 'welcome',
  role: 'system',
  content:
    '我是公司内部智能体。\n'
    + '· 点 + 发送文件：pdf / docx / pptx / xlsx / csv / txt / md / 图片；先暂存可提问或入库\n'
    + '· 可说「生成一份周报 Word / Markdown」「做个项目汇报 PPT」自动出文档\n'
    + '· 上传 .pptx 模板后，可说「根据这个模板生成 PPT」套用版式\n'
    + '· 可说「把公司 PPT 模板发我」下载知识库原文件\n'
    + '· 生成中可继续输入；点「停止」中断，或再发送一条作为补充（会打断当前回答）',
}

const messages = ref<UiMessage[]>([{ ...welcomeMessage }])
const downloading = ref<string | null>(null)
const previewing = ref<string | null>(null)
const previewOpen = ref(false)
const previewTitle = ref('')
const previewHtml = ref('')
const previewKind = ref('')
const previewFileId = ref<string | null>(null)
const previewLibraryId = ref<number | null>(null)
const previewOfficeSrc = ref<ArrayBuffer | string | null>(null)
const previewMode = ref<'html' | 'pptx' | 'docx'>('html')

const DOWNLOAD_RE = /\/api\/v1\/chat\/generated\/([a-f0-9]{8,32})/gi
const LIBRARY_RE = /\/api\/v1\/chat\/library\/(\d+)/gi

function extractDownloads(content: string): string[] {
  const ids = new Set<string>()
  // Normalize markdown/backticks so `` `/api/...` `` still matches.
  const normalized = (content || '').replace(/[`*]/g, '')
  for (const m of normalized.matchAll(DOWNLOAD_RE)) {
    ids.add(m[1].toLowerCase())
  }
  return [...ids]
}

function extractLibraryDownloads(content: string): number[] {
  const ids = new Set<number>()
  const normalized = (content || '').replace(/[`*]/g, '')
  for (const m of normalized.matchAll(LIBRARY_RE)) {
    ids.add(Number(m[1]))
  }
  return [...ids]
}

async function downloadGenerated(fileId: string) {
  downloading.value = fileId
  error.value = ''
  try {
    await api.downloadGenerated(fileId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}

async function downloadLibrary(documentId: number) {
  downloading.value = `lib-${documentId}`
  error.value = ''
  try {
    await api.downloadLibrary(documentId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '下载失败'
  } finally {
    downloading.value = null
  }
}

function kindFromFilename(filename: string, contentType = ''): 'pptx' | 'docx' | 'html' {
  const lower = (filename || '').toLowerCase()
  const ct = (contentType || '').toLowerCase()
  if (lower.endsWith('.pptx') || ct.includes('presentationml')) return 'pptx'
  if (lower.endsWith('.docx') || ct.includes('wordprocessingml')) return 'docx'
  return 'html'
}

async function openPreview(fileId: string) {
  previewing.value = fileId
  error.value = ''
  try {
    const data = await api.previewGenerated(fileId)
    previewFileId.value = data.file_id
    previewLibraryId.value = null
    previewTitle.value = data.filename
    previewKind.value = data.kind
    const mode = data.kind === 'pptx' || data.kind === 'docx' ? data.kind : 'html'
    previewMode.value = mode
    previewHtml.value = ''
    previewOfficeSrc.value = null
    if (mode === 'pptx' || mode === 'docx') {
      const file = await api.fetchGeneratedBuffer(fileId)
      previewOfficeSrc.value = file.buffer
      previewTitle.value = file.filename || data.filename
    } else {
      previewHtml.value = data.html
    }
    previewOpen.value = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : '预览失败'
  } finally {
    previewing.value = null
  }
}

async function openLibraryPreview(documentId: number) {
  previewing.value = `lib-${documentId}`
  error.value = ''
  try {
    const file = await api.fetchLibraryBuffer(documentId)
    const mode = kindFromFilename(file.filename, file.contentType)
    if (mode === 'html') {
      throw new Error('该文件类型暂不支持版式预览，请直接下载')
    }
    previewFileId.value = null
    previewLibraryId.value = documentId
    previewTitle.value = file.filename
    previewKind.value = mode
    previewMode.value = mode
    previewHtml.value = ''
    previewOfficeSrc.value = file.buffer
    previewOpen.value = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : '预览失败'
  } finally {
    previewing.value = null
  }
}

function closePreview() {
  previewOpen.value = false
  previewHtml.value = ''
  previewOfficeSrc.value = null
  previewFileId.value = null
  previewLibraryId.value = null
  previewMode.value = 'html'
}

async function downloadFromPreview() {
  if (previewFileId.value) {
    await downloadGenerated(previewFileId.value)
    return
  }
  if (previewLibraryId.value != null) {
    await downloadLibrary(previewLibraryId.value)
  }
}

function onOfficeRendered() {
  // no-op; reserved for loading indicator
}

function onOfficeError() {
  error.value = '版式预览渲染失败，请改用下载后用 PowerPoint / WPS 打开'
}
const activeTitle = computed(() => {
  if (sessionId.value == null) return '新对话'
  const hit = sessions.value.find((s) => s.id === sessionId.value)
  return hit?.title || `会话 #${sessionId.value}`
})

function welcomeOnly(): UiMessage[] {
  return [{ ...welcomeMessage, id: `welcome-${Date.now()}` }]
}

async function refreshSessions() {
  try {
    sessions.value = await api.sessions()
  } catch (e) {
    // Non-fatal: chat still works without the sidebar list.
    console.warn('load sessions failed', e)
  }
}

function abortChatRequest() {
  chatAbort.value?.abort()
  chatAbort.value = null
}

function stopGenerating() {
  if (!sending.value) return
  chatGen.value += 1
  abortChatRequest()
  sending.value = false
  messages.value.push({
    id: `stop-${Date.now()}`,
    role: 'system',
    content: '已停止生成。你可以补充说明后继续提问。',
  })
  void scrollBottom()
}

function startNewChat() {
  if (uploading.value || loadingSession.value) return
  if (sending.value) stopGenerating()
  sessionId.value = null
  error.value = ''
  input.value = ''
  messages.value = welcomeOnly()
}

async function openSession(id: number) {
  if (uploading.value || loadingSession.value) return
  if (sessionId.value === id) return
  if (sending.value) stopGenerating()
  loadingSession.value = true
  error.value = ''
  try {
    const rows = await api.messages(id)
    sessionId.value = id
    const mapped: UiMessage[] = rows.map((row) => ({
      id: `m-${row.id}`,
      role: (row.role === 'user' || row.role === 'assistant' || row.role === 'system'
        ? row.role
        : 'system') as UiMessage['role'],
      content: row.content,
      citations: row.citations,
    }))
    messages.value = mapped.length ? mapped : welcomeOnly()
    await scrollBottom()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载会话失败'
  } finally {
    loadingSession.value = false
  }
}

function askDeleteSession(id: number, ev?: Event) {
  ev?.stopPropagation()
  if (uploading.value || loadingSession.value || deleting.value) return
  const target = sessions.value.find((s) => s.id === id)
  if (!target) return
  pendingDelete.value = target
}

function cancelDelete() {
  if (deleting.value) return
  pendingDelete.value = null
}

async function confirmDelete() {
  const target = pendingDelete.value
  if (!target || deleting.value) return
  deleting.value = true
  error.value = ''
  try {
    await api.deleteSession(target.id)
    sessions.value = sessions.value.filter((s) => s.id !== target.id)
    pendingDelete.value = null
    if (sessionId.value === target.id) {
      startNewChat()
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = false
  }
}

async function scrollBottom() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || uploading.value || loadingSession.value) return

  // 再发一条 = 打断当前生成，把新内容当作补充/改问
  const myGen = ++chatGen.value
  abortChatRequest()

  input.value = ''
  error.value = ''
  messages.value.push({ id: `u-${Date.now()}`, role: 'user', content: text })
  const ac = new AbortController()
  chatAbort.value = ac
  sending.value = true
  await scrollBottom()
  try {
    const res = await api.chat(text, sessionId.value, { signal: ac.signal })
    if (myGen !== chatGen.value) return
    sessionId.value = res.session_id
    messages.value.push({
      id: `a-${Date.now()}`,
      role: 'assistant',
      content: res.answer,
      citations: res.citations,
    })
    await refreshSessions()
  } catch (e) {
    if (myGen !== chatGen.value) return
    if (isAbortError(e)) return
    error.value = e instanceof Error ? e.message : '发送失败'
  } finally {
    if (myGen === chatGen.value) {
      sending.value = false
      if (chatAbort.value === ac) chatAbort.value = null
    }
    await scrollBottom()
  }
}

function onComposerSubmit() {
  void send()
}

function openUpload() {
  if (sending.value) stopGenerating()
  fileInput.value?.click()
}

async function onFilePicked(e: Event) {
  const inputEl = e.target as HTMLInputElement
  const file = inputEl.files?.[0]
  inputEl.value = ''
  if (!file) return

  uploading.value = true
  error.value = ''
  messages.value.push({
    id: `u-up-${Date.now()}`,
    role: 'user',
    content: `发送文件：${file.name}`,
  })
  await scrollBottom()
  try {
    const doc = await api.upload(file, {
      note: file.name,
      sessionId: sessionId.value,
    })
    sessionId.value = doc.session_id
    messages.value.push({
      id: `s-${Date.now()}`,
      role: 'system',
      content: doc.tip,
    })
    await refreshSessions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '上传失败'
  } finally {
    uploading.value = false
    await scrollBottom()
  }
}

onMounted(() => {
  void refreshSessions()
  window.addEventListener('keydown', onGlobalKeydown)
})

function onGlobalKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && previewOpen.value) {
    closePreview()
    return
  }
  if (e.key === 'Escape' && pendingDelete.value && !deleting.value) {
    cancelDelete()
    return
  }
  if (e.key === 'Escape' && sending.value) {
    stopGenerating()
  }
}

onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  abortChatRequest()
})
</script>

<template>
  <div class="chat-layout">
    <aside class="session-rail" aria-label="会话列表">
      <button
        class="new-chat"
        type="button"
        :class="{ drafting: sessionId == null }"
        :disabled="uploading || loadingSession"
        @click="startNewChat"
      >
        <span class="plus" aria-hidden="true">+</span>
        新对话
      </button>
      <p class="rail-label">历史对话</p>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: sessionId === s.id }"
        >
          <button
            class="session-main"
            type="button"
            :disabled="uploading || loadingSession"
            :title="s.title"
            @click="openSession(s.id)"
          >
            {{ s.title }}
          </button>
          <button
            class="session-del"
            type="button"
            aria-label="删除对话"
            title="删除"
            :disabled="uploading || loadingSession || deleting"
            @click="askDeleteSession(s.id, $event)"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M9 3h6a1 1 0 0 1 1 1v1h4v2h-1v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7H4V5h4V4a1 1 0 0 1 1-1Zm1 2v0h4V5h-4Zm-2 4v10h2V9H8Zm3 0v10h2V9h-2Zm3 0v10h2V9h-2Z"
              />
            </svg>
          </button>
        </div>
        <p v-if="!sessions.length" class="empty-sessions">暂无历史对话</p>
      </div>
    </aside>

    <div class="chat">
      <div class="chat-toolbar">
        <span class="toolbar-hint">{{ activeTitle }}</span>
      </div>
      <div ref="scroller" class="messages">
        <article
          v-for="msg in messages"
          :key="msg.id"
          class="bubble"
          :class="msg.role"
        >
          <pre>{{ msg.content }}</pre>
          <div
            v-if="extractDownloads(msg.content).length || extractLibraryDownloads(msg.content).length"
            class="downloads"
          >
            <template v-for="fid in extractDownloads(msg.content)" :key="`${msg.id}-${fid}`">
              <button
                class="btn btn-ghost download-btn"
                type="button"
                :disabled="previewing === fid"
                @click="openPreview(fid)"
              >
                {{ previewing === fid ? '预览中…' : '预览' }}
              </button>
              <button
                class="btn btn-ghost download-btn"
                type="button"
                :disabled="downloading === fid"
                @click="downloadGenerated(fid)"
              >
                {{ downloading === fid ? '下载中…' : '下载' }}
              </button>
            </template>
            <template v-for="did in extractLibraryDownloads(msg.content)" :key="`${msg.id}-lib-${did}`">
              <button
                class="btn btn-ghost download-btn"
                type="button"
                :disabled="previewing === `lib-${did}`"
                @click="openLibraryPreview(did)"
              >
                {{ previewing === `lib-${did}` ? '预览中…' : '预览' }}
              </button>
              <button
                class="btn btn-ghost download-btn"
                type="button"
                :disabled="downloading === `lib-${did}`"
                @click="downloadLibrary(did)"
              >
                {{ downloading === `lib-${did}` ? '下载中…' : '下载原文件' }}
              </button>
            </template>
          </div>
          <div v-if="msg.citations?.length" class="cites">
            <div v-for="c in msg.citations" :key="`${msg.id}-${c.document_id}-${c.score}`" class="cite">
              <strong>《{{ c.title }}》</strong>
              <span>{{ c.snippet }}</span>
            </div>
          </div>
        </article>
        <article v-if="sending" class="bubble assistant pending" aria-live="polite" aria-busy="true">
          <div class="typing">
            <span /><span /><span />
          </div>
          <p class="pending-text">正在思考…（可继续输入补充，或点停止 / Esc）</p>
        </article>
        <p v-if="loadingSession" class="muted uploading">正在加载会话…</p>
        <p v-if="uploading" class="muted uploading">正在上传并识别…</p>
        <p v-if="error" class="error">{{ error }}</p>
      </div>

      <form class="composer" @submit.prevent="onComposerSubmit">
        <button class="icon-btn" type="button" :disabled="uploading || loadingSession" title="上传文件" @click="openUpload">
          +
        </button>
        <input
          v-model="input"
          class="input"
          type="text"
          enterkeyhint="send"
          :placeholder="sending ? '可输入补充后回车发送（打断当前回答），或点停止' : '输入问题，或点 + 上传文件后提问'"
          :disabled="uploading || loadingSession"
        />
        <button
          v-if="sending"
          class="btn btn-ghost send stop-btn"
          type="button"
          title="停止生成（Esc）"
          @click="stopGenerating"
        >
          停止
        </button>
        <button
          class="btn btn-accent send"
          type="submit"
          :disabled="uploading || loadingSession || !input.trim()"
        >
          {{ sending ? '补充发送' : '发送' }}
        </button>
        <input
          ref="fileInput"
          class="hidden"
          type="file"
          accept=".pdf,.txt,.md,.csv,.xlsx,.docx,.pptx,.png,.jpg,.jpeg,.webp,application/pdf,text/plain,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,image/png,image/jpeg,image/webp"
          @change="onFilePicked"
        />
      </form>
    </div>

    <div
      v-if="pendingDelete"
      class="confirm-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-dialog-title"
      @click.self="cancelDelete"
    >
      <div class="confirm-card">
        <h2 id="delete-dialog-title">确定删除该对话吗？</h2>
        <p>删除后，聊天记录将无法恢复。</p>
        <div class="confirm-actions">
          <button class="confirm-cancel" type="button" :disabled="deleting" @click="cancelDelete">
            取消
          </button>
          <button class="confirm-ok" type="button" :disabled="deleting" @click="confirmDelete">
            {{ deleting ? '删除中…' : '删除' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="previewOpen"
      class="preview-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="preview-dialog-title"
      @click.self="closePreview"
    >
      <div class="preview-card" :class="{ office: previewMode !== 'html' }">
        <header class="preview-head">
          <div>
            <h2 id="preview-dialog-title">{{ previewTitle }}</h2>
            <p class="preview-meta">
              <template v-if="previewMode === 'pptx' || previewMode === 'docx'">
                {{ previewKind.toUpperCase() }} 原文件预览（接近下载后打开效果）
              </template>
              <template v-else>
                {{ previewKind.toUpperCase() }} 预览
              </template>
            </p>
          </div>
          <div class="preview-actions">
            <button class="btn btn-ghost download-btn" type="button" @click="downloadFromPreview">
              下载
            </button>
            <button class="btn btn-ghost download-btn" type="button" @click="closePreview">
              关闭
            </button>
          </div>
        </header>
        <div v-if="previewMode === 'html'" class="preview-body" v-html="previewHtml" />
        <div v-else class="preview-body office-body">
          <VueOfficePptx
            v-if="previewMode === 'pptx' && previewOfficeSrc"
            :src="previewOfficeSrc"
            class="office-viewer"
            @rendered="onOfficeRendered"
            @error="onOfficeError"
          />
          <VueOfficeDocx
            v-else-if="previewMode === 'docx' && previewOfficeSrc"
            :src="previewOfficeSrc"
            class="office-viewer"
            @rendered="onOfficeRendered"
            @error="onOfficeError"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-layout {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: row;
  align-items: stretch;
  overflow: hidden;
}

.session-rail {
  flex: 0 0 240px;
  width: 240px;
  min-height: 0;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 12px;
  border-right: 1px solid var(--line);
  background: #f5f7f8;
  overflow: hidden;
}

.new-chat {
  flex: 0 0 auto;
  width: 100%;
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fff;
  color: var(--ink);
  font-weight: 600;
  font-size: 0.92rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.new-chat .plus {
  font-size: 1.15rem;
  line-height: 1;
  color: var(--accent);
}

.new-chat:hover:not(:disabled) {
  background: #eef6f4;
  border-color: #c9e2dd;
}

.new-chat.drafting {
  background: #e8f3f1;
  border-color: #b7d9d3;
}

.new-chat:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.rail-label {
  margin: 8px 4px 0;
  font-size: 0.72rem;
  color: var(--muted);
  letter-spacing: 0.02em;
}

.session-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
  padding-right: 2px;
}

.session-item {
  flex: 0 0 auto;
  position: relative;
  display: flex;
  align-items: center;
  border-radius: 10px;
  min-height: 40px;
}

.session-main {
  flex: 1 1 auto;
  min-width: 0;
  text-align: left;
  border: none;
  background: transparent;
  padding: 10px 36px 10px 12px;
  cursor: pointer;
  color: var(--ink);
  font-size: 0.88rem;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-del {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease, background 0.12s ease, color 0.12s ease;
}

.session-item:hover .session-del,
.session-item:focus-within .session-del,
.session-item.active .session-del {
  opacity: 1;
  pointer-events: auto;
}

.session-del:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-soft);
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.8);
}

.session-item.active {
  background: #fff;
  box-shadow: 0 0 0 1px var(--line);
}

.session-main:disabled,
.session-del:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.empty-sessions {
  margin: 12px 8px;
  font-size: 0.78rem;
  color: var(--muted);
}

.chat {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: transparent;
}

.chat-toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 16px 0;
}

.toolbar-hint {
  font-size: 0.78rem;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.messages {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bubble {
  max-width: min(720px, 92%);
  border-radius: 16px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  background: #fff;
  box-shadow: 0 1px 0 rgba(11, 31, 42, 0.03);
}

.bubble.user {
  align-self: flex-end;
  background: #e8f3f1;
  border-color: #cfe4df;
}

.bubble.assistant,
.bubble.system {
  align-self: flex-start;
}

.bubble.system {
  background: #f7fafb;
  color: var(--muted);
}

.bubble pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  line-height: 1.55;
  font-size: 0.95rem;
  color: var(--ink);
}

.downloads {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.download-btn {
  font-size: 0.85rem;
}

.cites {
  margin-top: 8px;
  display: grid;
  gap: 6px;
}

.cite {
  font-size: 0.82rem;
  color: var(--muted);
  display: grid;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 10px;
  background: #f7fafb;
}

.composer {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(8px);
}

.input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px;
  background: #fff;
  color: var(--ink);
  outline: none;
}

.input:focus {
  border-color: #9bb8b3;
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
}

.input:disabled {
  opacity: 0.6;
  background: #f7fafb;
}

.icon-btn {
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 1.4rem;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ink);
}

.icon-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.send {
  flex: 0 0 auto;
  min-width: 72px;
  min-height: 44px;
}

.stop-btn {
  border: 1px solid var(--line);
  color: var(--ink);
  background: #fff;
}

.stop-btn:hover {
  border-color: #c45c5c;
  color: #a33;
}

.hidden {
  display: none;
}

.uploading {
  margin: 0;
  font-size: 0.9rem;
  color: var(--muted);
}

.pending {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
}

.pending-text {
  margin: 0;
  font-size: 0.9rem;
}

.typing {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #7a9290;
  animation: bounce 1.2s infinite ease-in-out;
}

.typing span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes bounce {
  0%,
  80%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  40% {
    opacity: 1;
    transform: translateY(-3px);
  }
}

.error {
  margin: 0;
  font-size: 0.9rem;
  color: var(--danger);
}

.confirm-layer {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(18, 24, 32, 0.42);
}

.confirm-card {
  width: min(360px, 100%);
  padding: 24px 22px 18px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(11, 31, 42, 0.18);
  text-align: center;
}

.confirm-card h2 {
  margin: 0 0 8px;
  font-size: 1.05rem;
  font-weight: 650;
  color: var(--ink);
}

.confirm-card p {
  margin: 0 0 20px;
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 1.5;
}

.confirm-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.confirm-cancel,
.confirm-ok {
  min-height: 40px;
  border-radius: 10px;
  border: none;
  font-weight: 600;
  font-size: 0.92rem;
}

.confirm-cancel {
  background: #f2f4f6;
  color: var(--ink);
}

.confirm-cancel:hover:not(:disabled) {
  background: #e8ecf0;
}

.confirm-ok {
  background: #ff3b30;
  color: #fff;
}

.confirm-ok:hover:not(:disabled) {
  background: #e6352b;
}

.confirm-cancel:disabled,
.confirm-ok:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.preview-layer {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(20, 28, 32, 0.45);
}

.preview-card {
  width: min(880px, 100%);
  max-height: min(86vh, 900px);
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 16px;
  border: 1px solid var(--line);
  box-shadow: 0 18px 48px rgba(20, 28, 32, 0.18);
  overflow: hidden;
}

.preview-card.office {
  width: min(1100px, 100%);
  max-height: min(92vh, 980px);
}

.preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line);
}

.preview-head h2 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--ink);
  word-break: break-all;
}

.preview-meta {
  margin: 4px 0 0;
  font-size: 0.8rem;
  color: var(--muted);
}

.preview-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.preview-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 18px 20px 24px;
  background: #fafbfb;
}

.preview-body.office-body {
  padding: 0;
  background: #1f2329;
}

.office-viewer {
  width: 100%;
  height: min(78vh, 860px);
  background: #1f2329;
}

.preview-body :deep(.doc-preview h1) {
  margin: 0 0 12px;
  font-size: 1.35rem;
}

.preview-body :deep(.doc-preview h2) {
  margin: 16px 0 8px;
  font-size: 1.1rem;
}

.preview-body :deep(.doc-preview h3) {
  margin: 12px 0 6px;
  font-size: 1rem;
}

.preview-body :deep(.doc-preview p),
.preview-body :deep(.doc-preview li) {
  margin: 0 0 8px;
  line-height: 1.55;
  color: var(--ink);
}

.preview-body :deep(.doc-preview li) {
  margin-left: 1.2rem;
  list-style: disc;
}

.preview-body :deep(.doc-preview table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0 14px;
  font-size: 0.9rem;
}

.preview-body :deep(.doc-preview td) {
  border: 1px solid var(--line);
  padding: 6px 8px;
}

.preview-body :deep(.slide) {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 12px;
}

.preview-body :deep(.notes) {
  color: var(--muted);
  font-size: 0.88rem;
}

@media (max-width: 720px) {
  .chat-layout {
    flex-direction: column;
  }

  .session-rail {
    flex: 0 0 auto;
    width: 100%;
    max-height: 132px;
    border-right: none;
    border-bottom: 1px solid var(--line);
    padding: 8px 10px;
  }

  .rail-label {
    display: none;
  }

  .new-chat {
    width: auto;
    align-self: flex-start;
    min-height: 34px;
    padding: 0 12px;
  }

  .session-list {
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .session-item {
    min-width: 148px;
  }

  .session-del {
    opacity: 1;
    pointer-events: auto;
  }

  .chat {
    flex: 1 1 auto;
  }
}
</style>
