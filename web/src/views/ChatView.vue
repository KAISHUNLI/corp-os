<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { api, type Citation } from '@/api/client'

interface UiMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  citations?: Citation[]
}

const sessionId = ref<number | null>(null)
const input = ref('')
const sending = ref(false)
const uploading = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const textOverride = ref('')
const materialKind = ref('invoice')
const showUploadSheet = ref(false)
const pendingFile = ref<File | null>(null)

const materialOptions = [
  { value: 'invoice', label: '发票' },
  { value: 'train_ticket', label: '车票/机票' },
  { value: 'travel_approval', label: '出差审批单' },
  { value: 'itinerary', label: '行程说明' },
  { value: 'other', label: '其他资料/制度' },
]

const messages = ref<UiMessage[]>([
  {
    id: 'welcome',
    role: 'system',
    content:
      '我是公司内部智能体。\n'
      + '· 普通报销材料（发票/车票）可直接上传预审\n'
      + '· 制度/通知/薪资/财报等重要文件上传后需老板或部门主管审批，通过后才入库\n'
      + '· 主管/老板可发送「待我审批」，再回复「批准 #单号」或「驳回 #单号」\n'
      + '· 不同身份看到的资料不同（财务可见薪资财报，老板可见全部）',
  },
])

async function scrollBottom() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  input.value = ''
  error.value = ''
  messages.value.push({ id: `u-${Date.now()}`, role: 'user', content: text })
  await scrollBottom()
  sending.value = true
  try {
    const res = await api.chat(text, sessionId.value)
    sessionId.value = res.session_id
    messages.value.push({
      id: `a-${Date.now()}`,
      role: 'assistant',
      content: res.answer,
      citations: res.citations,
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : '发送失败'
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

function openUpload() {
  fileInput.value?.click()
}

function onFilePicked(e: Event) {
  const inputEl = e.target as HTMLInputElement
  const file = inputEl.files?.[0]
  inputEl.value = ''
  if (!file) return
  pendingFile.value = file
  textOverride.value = ''
  const name = file.name
  if (/发票|invoice/i.test(name)) materialKind.value = 'invoice'
  else if (/车票|火车|机票|行程单/i.test(name)) materialKind.value = 'train_ticket'
  else if (/审批/i.test(name)) materialKind.value = 'travel_approval'
  else materialKind.value = 'invoice'
  showUploadSheet.value = true
}

async function confirmUpload() {
  if (!pendingFile.value) return
  uploading.value = true
  error.value = ''
  try {
    const doc = await api.upload(pendingFile.value, {
      note: pendingFile.value.name,
      textOverride: textOverride.value,
      kind: materialKind.value,
      sessionId: sessionId.value,
    })
    sessionId.value = doc.session_id
    showUploadSheet.value = false
    messages.value.push({
      id: `s-${Date.now()}`,
      role: 'system',
      content: `${doc.tip}\n已识别类型：${doc.kind} 《${doc.title}》`,
    })
    pendingFile.value = null
    await scrollBottom()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '上传失败'
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="chat">
    <div ref="scroller" class="messages">
      <article
        v-for="msg in messages"
        :key="msg.id"
        class="bubble"
        :class="msg.role"
      >
        <pre>{{ msg.content }}</pre>
        <div v-if="msg.citations?.length" class="cites">
          <div v-for="c in msg.citations" :key="`${msg.id}-${c.document_id}-${c.score}`" class="cite">
            <strong>《{{ c.title }}》</strong>
            <span>{{ c.snippet }}</span>
          </div>
        </div>
      </article>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <form class="composer" @submit.prevent="send">
      <button class="icon-btn" type="button" :disabled="uploading" title="上传发票/车票等" @click="openUpload">
        +
      </button>
      <input
        v-model="input"
        class="input"
        type="text"
        enterkeyhint="send"
        placeholder="例如：我这些材料能不能报销成功？还缺什么？"
        :disabled="sending"
      />
      <button class="btn btn-accent send" type="submit" :disabled="sending || !input.trim()">
        发送
      </button>
      <input ref="fileInput" class="hidden" type="file" accept="image/*,.pdf,.txt,.md,.png,.jpg,.jpeg" @change="onFilePicked" />
    </form>

    <div v-if="showUploadSheet" class="sheet">
      <div class="panel panel-pad stack sheet-card">
        <strong>上传报销/制度材料</strong>
        <p class="muted">{{ pendingFile?.name }}</p>
        <div class="field">
          <label for="kind">材料类型</label>
          <select id="kind" v-model="materialKind">
            <option v-for="opt in materialOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>
        <div class="field">
          <label for="override">补充说明/识别文本（图片可先手填关键信息）</label>
          <textarea id="override" v-model="textOverride" placeholder="例如：上海-北京高铁票，金额 553 元" />
        </div>
        <div class="row">
          <button class="btn btn-ghost btn-block" type="button" @click="showUploadSheet = false">取消</button>
          <button class="btn btn-primary btn-block" type="button" :disabled="uploading" @click="confirmUpload">
            {{ uploading ? '上传中…' : '确认上传' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  width: min(860px, 100%);
  margin: 0 auto;
}

.messages {
  flex: 1;
  overflow: auto;
  padding: 16px 16px 12px;
  display: grid;
  gap: 12px;
  align-content: start;
}

.bubble {
  max-width: min(100%, 680px);
  border-radius: 16px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  background: #fff;
}

.bubble.user {
  justify-self: end;
  background: #0b1f2a;
  color: #eef7f5;
  border-color: transparent;
}

.bubble.system {
  background: #f3f7f8;
}

.bubble pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: inherit;
  line-height: 1.55;
}

.cites {
  margin-top: 10px;
  display: grid;
  gap: 8px;
}

.cite {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(15, 118, 110, 0.08);
  font-size: 0.86rem;
}

.bubble.user .cite {
  background: rgba(255, 255, 255, 0.08);
}

.composer {
  position: sticky;
  bottom: 0;
  display: grid;
  grid-template-columns: 44px 1fr auto;
  gap: 8px;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom, 0px));
  background: rgba(238, 242, 244, 0.94);
  border-top: 1px solid var(--line);
  backdrop-filter: blur(8px);
}

.icon-btn {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #fff;
  font-size: 1.4rem;
  line-height: 1;
}

.input {
  min-height: 44px;
  border-radius: 12px;
  border: 1px solid var(--line);
  padding: 0 12px;
  background: #fff;
}

.send {
  min-width: 72px;
}

.hidden {
  display: none;
}

.error {
  color: var(--danger);
  margin: 0;
}

.sheet {
  position: fixed;
  inset: 0;
  background: rgba(11, 31, 42, 0.35);
  display: grid;
  align-items: end;
  padding: 16px;
  z-index: 40;
}

.sheet-card {
  width: min(560px, 100%);
  margin: 0 auto 8px;
}

@media (min-width: 768px) {
  .sheet {
    align-items: center;
  }
}
</style>
