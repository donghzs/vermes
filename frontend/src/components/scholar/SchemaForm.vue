<script setup>
// 通用 JSON Schema → 表单组件。
//
// 决策 #5（用户 2026-07-25 评审）：
//  - P0c-1 支持 string / integer / boolean + enum（nested object 留 P0c-2）。
//  - project_id 字段：从 useScholarStore.currentProjectId 自动填充，UI 不渲染输入框。
import { ref, reactive, computed, watch } from 'vue'
import { useScholarStore } from '../../stores/scholar'

const props = defineProps({
  schema: { type: Object, required: true },
})
const emit = defineEmits(['submit'])

const scholar = useScholarStore()

const properties = computed(() => (props.schema?.parameters?.properties) || {})
const required = computed(() => (props.schema?.parameters?.required) || [])
const propEntries = computed(() => Object.entries(properties.value).filter(([k]) => k !== 'project_id'))

const form = reactive({})
const error = ref('')

function initForm() {
  // 清空并依据 schema 初始化默认值
  for (const k of Object.keys(form)) delete form[k]
  for (const [k, v] of Object.entries(properties.value)) {
    if (k === 'project_id') continue
    if (v.default !== undefined) form[k] = v.default
    else if (v.type === 'boolean') form[k] = false
    else if (v.type === 'integer') form[k] = (v.minimum !== undefined ? v.minimum : '')
    else form[k] = ''
  }
}
watch(() => props.schema, initForm, { immediate: true })

function fieldType(v) {
  if (v.enum) return 'enum'
  return v.type || 'string'
}

function buildArgs() {
  const args = {}
  for (const [k, v] of Object.entries(properties.value)) {
    if (k === 'project_id') {
      if (scholar.currentProjectId != null) args[k] = scholar.currentProjectId
      continue
    }
    let val = form[k]
    if (v.type === 'integer') {
      if (val === '' || val === null || val === undefined) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
      val = Number(val)
    } else if (v.type === 'boolean') {
      val = !!val
    } else {
      if (val === '' || val === null || val === undefined) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
    }
    args[k] = val
  }
  return args
}

function onSubmit() {
  error.value = ''
  try {
    const args = buildArgs()
    emit('submit', args)
  } catch (e) {
    error.value = e.message
  }
}

function onReset() {
  error.value = ''
  initForm()
}
</script>

<template>
  <form class="space-y-3" @submit.prevent="onSubmit">
    <div
      v-for="[key, def] in propEntries"
      :key="key"
      class="flex flex-col gap-1"
    >
      <label class="text-xs font-medium text-gray-600 dark:text-gray-300">
        {{ key }}
        <span v-if="required.includes(key)" class="text-red-500">*</span>
        <span class="text-gray-400 font-normal">（{{ def.type || 'string' }}）</span>
      </label>

      <!-- enum → select -->
      <select
        v-if="fieldType(def) === 'enum'"
        v-model="form[key]"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
      >
        <option v-for="opt in def.enum" :key="opt" :value="opt">{{ opt }}</option>
      </select>

      <!-- boolean → checkbox -->
      <label v-else-if="def.type === 'boolean'" class="inline-flex items-center gap-2 text-sm">
        <input type="checkbox" v-model="form[key]" class="rounded" />
        <span class="text-gray-500">{{ def.description }}</span>
      </label>

      <!-- integer → number -->
      <input
        v-else-if="def.type === 'integer'"
        type="number"
        v-model="form[key]"
        :min="def.minimum"
        :max="def.maximum"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
      />

      <!-- string → textarea -->
      <textarea
        v-else
        v-model="form[key]"
        rows="4"
        :placeholder="def.description"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-mono"
      ></textarea>

      <p v-if="def.description && def.type !== 'boolean'" class="text-xs text-gray-400 leading-snug">
        {{ def.description }}
      </p>
    </div>

    <p v-if="error" class="text-sm text-red-500">{{ error }}</p>

    <div class="flex gap-2 pt-1">
      <button
        type="submit"
        class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
      >
        运行工具
      </button>
      <button
        type="button"
        @click="onReset"
        class="px-4 py-2 rounded-lg bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-sm transition"
      >
        重置
      </button>
    </div>
  </form>
</template>
