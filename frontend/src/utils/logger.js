/**
 * Vermes 日志工具 — 开发环境全量输出，生产环境静默 warn/log
 *
 * 用法：import { logger } from '@/utils/logger'
 *       logger.warn('[Vermes] 配额刷新失败:', e)
 */

const isProd = import.meta.env.PROD
const noop = () => {}

export const logger = {
  /** 始终输出 — 生产环境也不静默 */
  error: (...args) => console.error(...args),

  /** 开发环境输出，生产静默 */
  warn: isProd ? noop : (...args) => console.warn(...args),

  /** 开发环境输出，生产静默 */
  log: isProd ? noop : (...args) => console.log(...args),

  /** 开发环境输出，生产静默 */
  info: isProd ? noop : (...args) => console.info(...args),
}
