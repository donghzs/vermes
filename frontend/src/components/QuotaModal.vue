<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { toast } from '../utils/toast'

const router = useRouter()
const chat = useChatStore()

const props = defineProps({
  serverQuota: Object,
  referralCode: String,
})

const emit = defineEmits(['wechatLogin', 'copyReferralCode'])

function close() {
  chat.showQuotaModal = false
}
</script>

<template>
  <div v-if="chat.showQuotaModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="close">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">

      <!-- 未登录弹窗 -->
      <template v-if="chat.quotaModalType === 'need_login'">
        <div class="text-4xl mb-3">🔐</div>
        <h3 class="font-bold text-lg mb-2">需要登录后使用</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">免费体验仅限微信登录用户</p>
        <p class="text-xs text-gray-400 mb-5">登录后每天可免费使用 500 积分</p>
        <div class="flex flex-col gap-3">
          <button @click="emit('wechatLogin')"
            class="w-full py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm font-medium transition">
            🟢 微信登录（500 积分/天）
          </button>
          <button @click="close(); router.push('/settings')"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            🔑 配置自己的 API Key
          </button>
          <button @click="close" class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">关闭</button>
        </div>
      </template>

      <!-- 免费体验已过期 -->
      <template v-else-if="chat.quotaModalType === 'trial_expired'">
        <div class="text-4xl mb-3">⏰</div>
        <h3 class="font-bold text-lg mb-2">免费体验已过期</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-5">免费体验截止至 2026年6月26日</p>
        <div class="flex flex-col gap-3">
          <button @click="close(); router.push('/settings')"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            🔑 配置自己的 API Key
          </button>
          <button @click="close" class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">关闭</button>
        </div>
      </template>

      <!-- 今日积分已用完 -->
      <template v-else-if="chat.quotaModalType === 'wechat_expired'">
        <div class="text-4xl mb-3">⏰</div>
        <h3 class="font-bold text-lg mb-2">今日额度已用完</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">微信登录用户每天 500 积分</p>
        <p class="text-xs text-amber-500 mb-5">⏰ 每日积分凌晨自动重置</p>
        <div class="flex flex-col gap-3">
          <button @click="close"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            ⏰ 明天再来（凌晨重置）
          </button>
          <button @click="close(); router.push('/settings')"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            🔑 配置自己的 API Key
          </button>
          <button @click="close" class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">关闭</button>
        </div>
      </template>

      <!-- 默认兜底 -->
      <template v-else>
        <div class="text-4xl mb-3">💡</div>
        <h3 class="font-bold text-lg mb-2">提示</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-5">请登录或配置 API Key 继续使用</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">
          今日已用 {{ serverQuota?.spent_today || 0 }}/{{ serverQuota?.total_limit || 500 }} 积分
        </p>
        <p v-if="serverQuota?.bonus_points > 0" class="text-xs text-green-500 mb-1">
          🎁 推荐奖励: +{{ serverQuota.bonus_points }} 积分/天
        </p>
        <p class="text-xs text-amber-500 mb-5">⏰ 每日积分凌晨自动重置</p>
        <div class="flex flex-col gap-3">
          <button @click="emit('copyReferralCode')"
            class="w-full py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm font-medium transition">
            🎁 推荐朋友 +200 积分
          </button>
          <p v-if="referralCode" class="text-xs text-gray-400 -mt-1">
            你的推荐码: <span class="font-mono text-green-500">{{ referralCode }}</span>
            <button @click="emit('copyReferralCode')" class="ml-1 text-green-500 hover:underline">复制</button>
          </p>
          <button @click="close"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            ⏰ 明天再来（凌晨重置）
          </button>
          <button @click="close(); router.push('/settings')"
            class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
            🔑 配置自己的 API Key
          </button>
          <button @click="close" class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">关闭</button>
        </div>
      </template>
    </div>
  </div>
</template>
