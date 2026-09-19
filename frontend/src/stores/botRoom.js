import { defineStore } from 'pinia'
import api from '@/services/api'

// ③ Bot Mode P1 · 房间状态（独立 store，绝不污染单聊 sessions 列表）
export const useBotRoomStore = defineStore('botRoom', {
  state: () => ({
    rooms: [],            // [{id, title, channel, created_at, updated_at}]
    currentRoomId: null,  // 当前打开的房间 id
    timeline: [],         // [{id, author_type, author_ref, content, created_at, turn_session_id}]
    members: [],          // Phase 2：房间成员（@ 补全候选来源）
    loadingRooms: false,
    loadingTimeline: false,
    loadingMembers: false,
    sending: false,
    error: '',
    botModeDisabled: false,  // BOT_MODE_ENABLED 关闭 → 优雅降级（T7）
    streaming: {},           // 流式：{ [agent_id]: { text: 累计文本, active: bool } }（真群聊流式体验）
  }),
  getters: {
    currentRoom: (s) => s.rooms.find(r => r.id === s.currentRoomId) || null,

    // Phase 2 @ 补全候选：仅 agent 成员可被 @（human 成员不属于应答主体）。
    // 在此预处理展示字段，让组件保持纯渲染：
    //  - insert：实际写入文本的词元。后端 ROOM_MENTION_RE
    //    （vermes_cli/botmode/core.py，对齐 ① A2A _HANDLE_RE）**遇空白截断**，
    //    故含空格的 name 无法被完整匹配 → 退化为 ref_id（id 恒无空格）。
    //  - hue：优先用 T1 预留的 profile.hue（seed: researcher=210 / coder=140）；
    //    为 0 或缺失时按 ref_id 哈希兜底，保证每个 agent 颜色稳定且可区分。
    //  - initial：头像首字（中文取首字 / 英文取首字母）。
    mentionCandidates: (s) => (s.members || [])
      .filter(m => m.member_type === 'agent' || m.member_type === 'secretary')
      .map((m) => {
        // ⑭ 组织岗位名优先：组织成立后成员以「岗位/职位」示人（产品经理/QA），
        // 无岗位才回落 agent 本名。insert 同步用岗位名，群里 @产品经理 即命中。
        const name = m.role_name || m.name || m.ref_id || ''
        const insert = /\s/.test(name) ? (m.ref_id || name) : name
        let hue = Number(m.hue) || 0
        if (!hue) {
          let h = 0
          for (const ch of String(m.ref_id || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
          hue = h
        }
        return { ...m, name, insert, hue, initial: String(name || '?').slice(0, 1) }
      }),

    // 头像色/首字查表：时间线按 author_ref 复用同一套视觉（补全与时间线一致）
    memberByRef: (s) => {
      const map = {}
      for (const m of s.members || []) {
        // ⑭ 岗位名优先（与 mentionCandidates 一致）
        const name = m.role_name || m.name || m.ref_id || ''
        let hue = Number(m.hue) || 0
        if (!hue) {
          let h = 0
          for (const ch of String(m.ref_id || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
          hue = h
        }
        map[m.ref_id] = { name, hue, initial: String(name || '?').slice(0, 1), member_type: m.member_type, role_name: m.role_name }
      }
      return map
    },
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
      this.ensureRoomRealtime()
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
    async createRoom(name, members = [], extra = {}) {
      if (this.botModeDisabled) return { ok: false, error: 'bot mode disabled' }
      const r = await api.createBotRoom(name, members, extra)
      if (r && r.ok) {
        await this.loadRooms()
        this.currentRoomId = r.room_id
        await Promise.all([this.loadTimeline(r.room_id), this.loadMembers(r.room_id)])
      }
      return r
    },
    async updateRoom(patch) {
      const rid = this.currentRoomId
      if (!rid || this.botModeDisabled) return { ok: false, error: 'no room' }
      const r = await api.updateBotRoom(rid, patch)
      if (r && r.ok) await this.loadRooms()
      return r
    },
    async deleteRoom(id) {
      // ⚙️ 2026-09-07 解散群：删群 + 级联清理（后端 delete_bot_room）
      const rid = id || this.currentRoomId
      if (!rid || this.botModeDisabled) return { ok: false, error: 'no room' }
      const r = await api.deleteBotRoom(rid)
      if (r && r.ok) {
        this.rooms = this.rooms.filter(x => x.id !== rid)
        if (this.currentRoomId === rid) {
          this.currentRoomId = ''
          this.timeline = []
          this.members = []
        }
      }
      return r
    },
    async addMember(refId) {
      const rid = this.currentRoomId
      if (!rid) return { ok: false, error: 'no room' }
      const r = await api.addBotRoomMember(rid, refId)
      if (r && r.ok) await this.loadMembers(rid)
      return r
    },
    async removeMember(refId) {
      const rid = this.currentRoomId
      if (!rid) return { ok: false, error: 'no room' }
      const r = await api.removeBotRoomMember(rid, refId)
      if (r && r.ok) await this.loadMembers(rid)
      return r
    },
    async selectRoom(id) {
      this.currentRoomId = id
      await Promise.all([this.loadTimeline(id), this.loadMembers(id)])
    },
    // Phase 2：拉取房间成员（@ 补全候选）。失败静默——候选为空时
    // 仅失去补全能力，不阻断消息发送（后端仍按全局 profile 解析 @mention）。
    async loadMembers(id) {
      const rid = id || this.currentRoomId
      if (!rid) return
      this.loadingMembers = true
      try {
        const r = await api.listBotRoomMembers(rid)
        if (r && r.ok) this.members = r.members || []
      } catch (e) {
        if (this._isBotDisabled(e)) this.botModeDisabled = true
      } finally {
        this.loadingMembers = false
      }
      return this.members
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
    /** C1：SSE 实时订阅（WS 断开时兜底；幂等） */
    ensureRoomRealtime() {
      if (this._sse || typeof EventSource === 'undefined') return
      try {
        const es = new EventSource('/api/channels/events')
        this._sse = es
        es.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data)
            if (msg && msg.type === 'room_update') {
              window.dispatchEvent(new CustomEvent('vermes:room_update', { detail: msg }))
            }
          } catch (_) {}
        }
        es.onerror = () => { /* EventSource 自带重连 */ }
      } catch (_) { /* 非浏览器 */ }
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
    // C1：另有 SSE /api/channels/events 兜底（ensureRoomRealtime）
    async onRoomUpdate(msg) {
      if (!msg || msg.type !== 'room_update') return
      const topicRoom = (msg.topic || '').replace(/^room:/, '')
      if (msg.event === 'room_created') {
        await this.loadRooms()
        return
      }
      if (msg.event === 'room_updated') {
        // 公告/任务/标题变更：重拉列表（房间少，简单正确）
        await this.loadRooms()
        return
      }
      if (msg.event === 'member_change') {
        await this.loadRooms()
        // 成员变更直接影响 @ 补全候选 → 当前房间同步刷新成员列表
        if (topicRoom === this.currentRoomId) {
          await this.loadMembers(this.currentRoomId)
        }
        return
      }
      // ⚙️ 2026-09-07 解散群广播：本端清理（他端删除时同步消失）
      if (msg.event === 'room_deleted') {
        this.rooms = this.rooms.filter(x => x.id !== topicRoom)
        if (this.currentRoomId === topicRoom) {
          this.currentRoomId = ''
          this.timeline = []
          this.members = []
        }
        return
      }
      if (msg.event === 'room_message' && topicRoom === this.currentRoomId) {
        // 直接重拉时间线（P1 房间小，简单正确优先；未来可改为增量 merge）
        await this.loadTimeline(this.currentRoomId)
      }
      if (msg.event === 'room_message_delta' && topicRoom === this.currentRoomId) {
        // 流式：delta 阶段累积文本；start 阶段初始化气泡；最终 room_message 落库后由
        // 上面的 room_message 分支重拉完整时间线，此处清理 streaming 状态。
        const m = msg.message || {}
        const aid = m.agent_id || ''
        if (!aid) return
        if (m.phase === 'start') {
          this.streaming = { ...this.streaming, [aid]: { text: '', active: true } }
          return
        }
        if (m.phase === 'delta') {
          const cur = this.streaming[aid] || { text: '', active: true }
          this.streaming = { ...this.streaming, [aid]: { text: cur.text + (m.delta || ''), active: true } }
        }
        // ⑭ 组织流水线 token 级流式：后端每个执行者结束（成功或异常）都补发
        // phase='end'，此处显式收起气泡。否则子任务工件若未以
        // author_ref=<profile id> 的 agent 消息落库，「正在生成」气泡会永久悬挂。
        if (m.phase === 'end') {
          const next = { ...this.streaming }
          delete next[aid]
          this.streaming = next
        }
      }
      // 落库完成（room_message 且是 agent 消息）→ 清理对应 streaming 气泡
      if (msg.event === 'room_message' && topicRoom === this.currentRoomId) {
        const m = msg.message || {}
        if (m && m.author_type === 'agent' && m.author_ref) {
          const next = { ...this.streaming }
          delete next[m.author_ref]
          this.streaming = next
        }
      }
    },
  },
})
