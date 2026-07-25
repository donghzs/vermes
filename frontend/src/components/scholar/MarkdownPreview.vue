<script setup>
// 公共 Markdown 预览组件。
//
// 决策 #4（用户 2026-07-25 评审）：从 MessageList.vue 抽离 renderMd 逻辑到独立组件，
// 不修改 MessageList.vue（聊天核心，P0c 阶段不动）。渲染配置与 MessageList 保持一致：
// markdown-it + DOMPurify 加固 + highlight.js 代码高亮。
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { DOMPURIFY_BASE_CONFIG, enforceLinkSecurity } from '../../utils/security'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import html from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import java from 'highlight.js/lib/languages/java'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('html', html)
hljs.registerLanguage('css', css)
hljs.registerLanguage('java', java)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)

DOMPurify.addHook('afterSanitizeAttributes', enforceLinkSecurity)

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight: function (str, lang) {
    const lineCount = str.split('\n').length
    const isLong = lineCount > 20
    const codeCls = isLong ? ' hljs-long' : ''
    let codeHtml = ''
    if (lang && hljs.getLanguage(lang)) {
      try {
        codeHtml = hljs.highlight(str, { language: lang }).value
      } catch (__) {
        codeHtml = md.utils.escapeHtml(str)
      }
    } else {
      try {
        codeHtml = hljs.highlightAuto(str, ['javascript', 'python', 'bash', 'json', 'html', 'css']).value
      } catch (__) {
        codeHtml = md.utils.escapeHtml(str)
      }
    }
    const toggle = isLong
      ? `<div class="code-toggle" onclick="this.parentElement.classList.toggle('collapsed')"><span class="code-lang">${lang || 'code'}</span><span class="code-toggle-btn">${lineCount} 行 · 点击折叠/展开</span></div>`
      : (lang ? `<div class="code-lang-bar"><span class="code-lang">${lang}</span></div>` : '')
    return `<pre class="hljs${codeCls}">${toggle}<code>${codeHtml}</code></pre>`
  },
})

const defaultLinkRenderer = md.renderer.rules.link_open || function (tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return defaultLinkRenderer(tokens, idx, options, env, self)
}

const props = defineProps({
  content: { type: String, default: '' },
})

const rendered = computed(() => {
  if (!props.content) return ''
  try {
    return DOMPurify.sanitize(md.render(props.content), DOMPURIFY_BASE_CONFIG)
  } catch (e) {
    console.error('[MarkdownPreview] render failed:', e)
    return props.content.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
})
</script>

<template>
  <div class="markdown-body text-sm leading-relaxed" v-html="rendered"></div>
</template>
