import { ref, computed } from 'vue'
import { logger } from '@/utils/logger'
import { toast } from '../utils/toast'

/**
 * useQuota — 配额查询与推荐码 composable
 *
 * 将配额/推荐码的数据获取逻辑从 ChatView.vue 中拆分出来，
 * 遵循单一职责原则。
 *
 * 用法：
 *   const { serverQuota, referralCode, quotaDisplay,
 *           refreshQuota, loadReferralCode, copyReferralCode,
 *           setupQuotaEvents } = useQuota()
 *   onMounted(() => setupQuotaEvents())
 */
export function useQuota() {
  // ── 响应式状态 ──
  const serverQuota = ref({
    remaining: 200,
    total_limit: 200,
    spent_today: 0,
    bonus_points: 0,
    days_left: 31,
    is_wechat: false,
  })
  const referralCode = ref('')

  // ── 数据获取 ──
  async function refreshQuota() {
    try {
      const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
      if (!wechatOpenid) {
        serverQuota.value = {
          remaining: 0,
          total_limit: 500,
          spent_today: 0,
          bonus_points: 0,
          days_left: 0,
          is_wechat: false,
          need_login: true,
        }
        return
      }
      const resp = await fetch('/api/quota/check', {
        headers: { 'X-WeChat-Openid': wechatOpenid }
      })
      const data = await resp.json()
      if (data.success) serverQuota.value = data.data
    } catch (e) {
      logger.warn('[Vermes] 刷新配额失败:', e)
    }
  }

  async function loadReferralCode() {
    try {
      const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
      if (!wechatOpenid) return
      const resp = await fetch(
        '/api/quota/referral/code?wechat_openid=' + encodeURIComponent(wechatOpenid)
      )
      const data = await resp.json()
      if (data.success) referralCode.value = data.data
    } catch (e) {
      /* ignore */
    }
  }

  // ── 计算属性 ──
  const quotaDisplay = computed(() => {
    if (serverQuota.value.need_login) return { text: '🔐 登录后免费使用', remaining: 0 }
    const q = serverQuota.value
    return {
      text: `✨ ${q.remaining}/${q.total_limit} 积分 · ${q.days_left}天`,
      remaining: q.remaining,
    }
  })

  // ── 推荐码复制 ──
  function copyReferralCode() {
    if (!referralCode.value) return
    const text = `我在用 Vermes AI 助手，免费体验中！用我的推荐码 ${referralCode.value} 注册，我俩都能获得额外 200 积分/天。下载: https://vbit.top/vermes/#downloads`
    navigator.clipboard
      .writeText(text)
      .then(() => {
        toast.success('✅ 推荐码已复制到剪贴板！分享给朋友即可获得 +200 积分/天')
      })
      .catch(() => {
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        toast.success('✅ 推荐码已复制到剪贴板！')
      })
  }

  // ── 事件监听 ──
  let _quotaUpdatedHandler = null

  /**
   * 挂载全局事件监听（quota-updated 刷新配额）。
   * 调用方在 onMounted 中执行。
   */
  function setupQuotaEvents() {
    _quotaUpdatedHandler = () => refreshQuota()
    window.addEventListener('quota-updated', _quotaUpdatedHandler)
    refreshQuota()
    loadReferralCode()
  }

  /**
   * 卸载全局事件监听。
   * 调用方在 onUnmounted 中执行。
   */
  function teardownQuotaEvents() {
    if (_quotaUpdatedHandler) {
      window.removeEventListener('quota-updated', _quotaUpdatedHandler)
      _quotaUpdatedHandler = null
    }
  }

  return {
    serverQuota,
    referralCode,
    quotaDisplay,
    refreshQuota,
    loadReferralCode,
    copyReferralCode,
    setupQuotaEvents,
    teardownQuotaEvents,
  }
}
