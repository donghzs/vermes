import { defineStore } from 'pinia'
import api from '@/services/api'

// ③ Bot Mode P1 · 房间状态（独立 store，绝不污染单聊 sessions 列表）
export const useBotRoomStore = defineStore('botRoom', {
  state: () => ({
    rooms: [],            // [{id, title, channel, created_at, updated_at}]
    currentRoomId: null,  // 当前打开的房间 id
    timeline: [],         // [{id, author_type, author_ref, content, created_at, turn_session_id}]
    loadingRooms: false,
    loadingTimeline: false,
    sending: false,
    error: '',
    botModeDisabled: false,  // BOT_MODE_ENABLED 关闭 → 优雅降级（T7）
  }),
  getters: {
    currentRoom: (s) => s.rooms.find(r => r.id === s.currentRoomId) || null,
  },
  actions: {
    // 识别「Bot Mode 未启用」：开关关闭时路由未注册(404) 或处理器守卫(403 "bot mode disabled")
    _isBotDisabled(e) {
      const m = (e && e.message) || ''
      return m.includes('bot mode disabled') || m.startsWith('API 404')
    },
    async loadRooms() {
      this.loadingRooms = true
      this.error = ''
      try {
        const r = await api.listBotRooms()
        if (r && r.ok) { this.rooms = r.rooms || []; this.botModeDisabled = false }
        else this.error = (r && r.error) || '加载房间失败'
      } catch (e) {
        if (this._isBotDisabled(e)) this.botModeDisabled = true
        this.error = e.message || '加载房间失败'
      } finally {
        this.loadingRooms = false
      }
      return this.rooms
    },
    async createRoom(id, name) {
      if (this.botModeDisabled) return { ok: false, error: 'bot mode disabled' }
      const r = await api.createBotRoom(id, name)
      if (r && r.ok) {
        await this.loadRooms()
        this.currentRoomId = id
        await this.loadTimeline(id)
      }
      return r
    },
    async selectRoom(id) {
      this.currentRoomId = id
      await this.loadTimeline(id)
    },
    async loadTimeline(id) {
      const rid = id || this.currentRoomId
      if (!rid) return
      this.loadingTimeline = true
      try {
        const r = await api.getBotRoomTimeline(rid)
        if (r && r.ok) this.timeline = r.timeline || []
      } finally {
        this.loadingTimeline = false
      }
    },
    async sendMessage(text) {
      if (this.botModeDisabled) return
      if (!this.currentRoomId || !text || !text.trim()) return
      this.sending = true
      try {
        const r = await api.sendBotRoomMessage(this.currentRoomId, text)
        // 后端返回完整时间线（含 @mention 派生的 agent 回复）
        if (r && r.ok) this.timeline = r.timeline || []
      } finally {
        this.sending = false
      }
    },
    // WS room_update 回调（由 chat.js initChannelSync 经 CustomEvent 转发）
    async onRoomUpdate(msg) {
      if (!msg || msg.type !== 'room_update') return
      const topicRoom = (msg.topic || '').replace(/^room:/, '')
      if (msg.event === 'room_created' || msg.event === 'member_change') {
        await this.loadRooms()
        return
      }
      if (msg.event === 'room_message' && topicRoom === this.currentRoomId) {
        // 直接重拉时间线（P1 房间小，简单正确优先；未来可改为增量 merge）
        await this.loadTimeline(this.currentRoomId)
      }
    },
  },
})
