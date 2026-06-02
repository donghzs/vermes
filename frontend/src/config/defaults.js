/**
 * defaults.js — 应用默认配置
 *
 * 这些值仅在没有用户 localStorage 配置时作为回退使用。
 * 用户通过 Settings 同步 providers 后，实际配置来自后端。
 *
 * 修改默认模型只需改这一个文件。
 */

/** 默认模型 ID（未配置 provider 时使用） */
export const DEFAULT_MODEL_ID = 'agnes-2.0-flash'

/** 默认 provider ID */
export const DEFAULT_PROVIDER_ID = 'vbit'

/** 默认模型列表（未同步 provider 时的回退模型列表） */
export const DEFAULT_MODELS = [
  { id: DEFAULT_MODEL_ID, name: '✨ Agnes 2.0 Flash（免费）', provider: 'agnes' },
]
