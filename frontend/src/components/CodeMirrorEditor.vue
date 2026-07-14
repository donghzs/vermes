<template>
  <div ref="cmContainer" class="cm-editor-container w-full h-full"></div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, shallowRef } from 'vue'
import { EditorState, Compartment } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
// 不加载 language-data，论文写作只需 Markdown；代码块语法高亮用默认
import { HighlightStyle, syntaxHighlighting, defaultHighlightStyle, bracketMatching, foldGutter, indentOnInput } from '@codemirror/language'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  dark: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  onTabComplete: { type: Function, default: null },  // 外部 Tab 补全回调
})

const emit = defineEmits(['update:modelValue', 'input', 'mouseup', 'keyup', 'keydown'])

const cmContainer = ref(null)
const view = shallowRef(null)
const themeCompartment = new Compartment()
const readOnlyCompartment = new Compartment()

// 暗色主题
const darkTheme = EditorView.theme({
  '&': {
    backgroundColor: '#0a0a0a',
    color: '#e5e7eb',
  },
  '.cm-content': {
    caretColor: '#22c55e',
    fontFamily: '"SF Mono", "JetBrains Mono", "Fira Code", monospace',
    fontSize: '14px',
    lineHeight: '1.7',
    padding: '16px',
  },
  '.cm-gutters': {
    backgroundColor: '#111111',
    color: '#4b5563',
    border: 'none',
  },
  '.cm-activeLine': { backgroundColor: 'rgba(34,197,94,0.05)' },
  '.cm-activeLineGutter': { backgroundColor: 'rgba(34,197,94,0.1)', color: '#22c55e' },
  '.cm-selectionBackground': { backgroundColor: 'rgba(34,197,94,0.2)' },
  '&.cm-focused .cm-selectionBackground': { backgroundColor: 'rgba(34,197,94,0.3)' },
  '.cm-cursor': { borderLeftColor: '#22c55e' },
  '.cm-matchingBracket': { backgroundColor: 'rgba(34,197,94,0.2)' },
}, { dark: true })

// 亮色主题
const lightTheme = EditorView.theme({
  '&': {
    backgroundColor: '#ffffff',
    color: '#1f2937',
  },
  '.cm-content': {
    caretColor: '#22c55e',
    fontFamily: '"SF Mono", "JetBrains Mono", "Fira Code", monospace',
    fontSize: '14px',
    lineHeight: '1.7',
    padding: '16px',
  },
  '.cm-gutters': {
    backgroundColor: '#f9fafb',
    color: '#9ca3af',
    border: 'none',
  },
  '.cm-activeLine': { backgroundColor: 'rgba(34,197,94,0.05)' },
  '.cm-activeLineGutter': { backgroundColor: 'rgba(34,197,94,0.1)', color: '#16a34a' },
  '.cm-selectionBackground': { backgroundColor: 'rgba(34,197,94,0.15)' },
  '&.cm-focused .cm-selectionBackground': { backgroundColor: 'rgba(34,197,94,0.25)' },
  '.cm-cursor': { borderLeftColor: '#22c55e' },
  '.cm-matchingBracket': { backgroundColor: 'rgba(34,197,94,0.15)' },
})

// Placeholder 扩展
const placeholderExt = (text) => {
  const el = document.createElement('div')
  el.style.cssText = 'color: #9ca3af; pointer-events: none; font-size: 14px;'
  el.textContent = text
  return EditorView.contentAttributes.of({ 'aria-label': text })
}

onMounted(() => {
  const state = EditorState.create({
    doc: props.modelValue,
    extensions: [
      lineNumbers(),
      history(),
      drawSelection(),
      bracketMatching(),
      closeBrackets(),
      indentOnInput(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      foldGutter(),
      autocompletion(),
      EditorState.allowMultipleSelections.of(true),
      markdown({ base: markdownLanguage }),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([
        // Tab 补全优先级最高
        {
          key: 'Tab',
          preventDefault: true,
          run: (view) => {
            if (props.onTabComplete) {
              const handled = props.onTabComplete(view)
              return handled
            }
            return false
          }
        },
        indentWithTab,
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        ...completionKeymap,
      ]),
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ 'aria-label': props.placeholder || '编辑器' }),
      themeCompartment.of(props.dark ? darkTheme : lightTheme),
      readOnlyCompartment.of(EditorState.readOnly.of(props.readOnly)),
      // 同步 modelValue
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          emit('update:modelValue', update.state.doc.toString())
          emit('input', update.state.doc.toString())
        }
        if (update.selectionSet) {
          // 触发 mouseup/keyup 等效事件
          emit('mouseup', { target: { selectionStart: getSelectionStart(), selectionEnd: getSelectionEnd() } })
        }
        if (update.focusChanged) {
          // focus/blur 可在此处理
        }
      }),
      // keydown 事件
      EditorView.domEventHandlers({
        keydown: (event) => {
          emit('keydown', event)
          return false  // 不阻止默认行为
        },
        mouseup: (event) => {
          emit('mouseup', { target: { selectionStart: getSelectionStart(), selectionEnd: getSelectionEnd() } })
          return false
        },
        keyup: (event) => {
          emit('keyup', { target: { selectionStart: getSelectionStart(), selectionEnd: getSelectionEnd() } })
          return false
        },
      }),
    ],
  })

  view.value = new EditorView({
    state,
    parent: cmContainer.value,
  })
})

// 外部 modelValue 变化时更新编辑器（避免光标跳动）
watch(() => props.modelValue, (newVal) => {
  if (!view.value) return
  const current = view.value.state.doc.toString()
  if (newVal !== current) {
    view.value.dispatch({
      changes: { from: 0, to: current.length, insert: newVal }
    })
  }
})

// 主题切换
watch(() => props.dark, (isDark) => {
  if (!view.value) return
  view.value.dispatch({
    effects: themeCompartment.reconfigure(isDark ? darkTheme : lightTheme)
  })
})

// readOnly 切换
watch(() => props.readOnly, (isRO) => {
  if (!view.value) return
  view.value.dispatch({
    effects: readOnlyCompartment.reconfigure(EditorState.readOnly.of(isRO))
  })
})

// 暴露方法
const getSelectionStart = () => {
  if (!view.value) return 0
  const sel = view.value.state.selection.main
  return sel.from
}

const getSelectionEnd = () => {
  if (!view.value) return 0
  const sel = view.value.state.selection.main
  return sel.to
}

const getSelectedText = () => {
  if (!view.value) return ''
  const sel = view.value.state.selection.main
  return view.value.state.sliceDoc(sel.from, sel.to)
}

const setSelection = (start, end) => {
  if (!view.value) return
  view.value.dispatch({
    selection: { anchor: start, head: end },
    scrollIntoView: true,
  })
}

const focus = () => {
  view.value?.focus()
}

const insertText = (text, from, to) => {
  if (!view.value) return
  if (from === undefined) {
    // 在光标处插入
    view.value.dispatch(view.value.state.replaceSelection(text))
  } else {
    view.value.dispatch({
      changes: { from: from ?? 0, to: to ?? from ?? 0, insert: text }
    })
  }
}

const getCursorPos = () => {
  if (!view.value) return 0
  return view.value.state.selection.main.head
}

defineExpose({
  getSelectionStart,
  getSelectionEnd,
  getSelectedText,
  setSelection,
  focus,
  insertText,
  getCursorPos,
  view,
})

onBeforeUnmount(() => {
  view.value?.destroy()
})
</script>

<style scoped>
.cm-editor-container {
  position: relative;
}

.cm-editor-container :deep(.cm-editor) {
  height: 100%;
  font-size: 14px;
}

.cm-editor-container :deep(.cm-scroller) {
  overflow: auto;
  font-family: "SF Mono", "JetBrains Mono", "Fira Code", monospace;
}

.cm-editor-container :deep(.cm-editor.cm-focused) {
  outline: none;
}

.cm-editor-container :deep(.cm-lineNumbers .cm-gutterElement) {
  font-size: 12px;
  padding: 0 8px;
}
</style>
