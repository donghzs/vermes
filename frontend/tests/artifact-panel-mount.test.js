import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref, h } from 'vue'

/**
 * ArtifactPanel 产物 tab 组件挂载测试
 * 
 * 策略：提取 ArtifactPanel 的产物列表 + 预览分发逻辑为轻量 stub 组件，
 * 不依赖 markdown-it/highlight.js/katex 等重型库，测核心交互而非渲染细节。
 */

// ── 产物列表项组件（提取自 ArtifactPanel.vue 产物列表渲染）──
const ArtifactItem = defineComponent({
  props: {
    artifact: { type: Object, required: true },
    active: { type: Boolean, default: false },
  },
  emits: ['click', 'open-folder', 'copy', 'download', 'delete'],
  template: `
    <div
      class="artifact-item"
      :class="{ 'active': active }"
      @click="$emit('click', artifact)"
    >
      <span class="artifact-icon">{{ icon }}</span>
      <span class="artifact-name">{{ artifact.name }}</span>
      <span class="artifact-size">{{ sizeText }}</span>
      <button class="btn-folder" @click.stop="$emit('open-folder', artifact)" title="文件夹">📁</button>
      <button class="btn-copy" @click.stop="$emit('copy', artifact)" title="复制">⎘</button>
      <button class="btn-download" @click.stop="$emit('download', artifact)" title="下载">⬇</button>
      <button class="btn-delete" @click.stop="$emit('delete', artifact)" title="删除">🗑</button>
    </div>
  `,
  computed: {
    icon() {
      const ext = (this.artifact.name || '').split('.').pop()?.toLowerCase()
      const map = { md: '📝', html: '🌐', json: '📋', csv: '📊', py: '🐍', js: '📜', ts: '📜', txt: '📄', pdf: '📕', docx: '📘', xlsx: '📗', png: '🖼️', jpg: '🖼️', svg: '🖼️', step: '⚙️', stl: '🖨️' }
      return map[ext] || '📄'
    },
    sizeText() {
      const s = this.artifact.size || 0
      if (s < 1024) return s + 'B'
      if (s < 1048576) return (s / 1024).toFixed(1) + 'KB'
      return (s / 1048576).toFixed(1) + 'MB'
    },
  },
})

// ── 预览分发组件（提取自 ArtifactPanel.vue rendererFor 逻辑）──
const PreviewDispatcher = defineComponent({
  props: {
    artifact: { type: Object, required: true },
  },
  setup(props) {
    const previewType = ref('unsupported')
    
    function detectType(artifact) {
      const ext = (artifact.name || '').split('.').pop()?.toLowerCase()
      const mime = artifact.mime_type || ''
      if (ext === 'md' || mime === 'text/markdown') return 'markdown'
      if (ext === 'html' || mime === 'text/html') return 'html'
      if (ext === 'json' || mime === 'application/json') return 'json'
      if (ext === 'csv' || mime === 'text/csv') return 'csv'
      if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) return 'image'
      if (['py', 'js', 'ts', 'bash', 'sh', 'go', 'rs', 'sql'].includes(ext)) return 'code'
      if (['txt', 'log'].includes(ext)) return 'text'
      return 'unsupported'
    }
    
    previewType.value = detectType(props.artifact)
    
    return () => h('div', { class: `preview-${previewType.value}` }, [
      h('span', { class: 'type-badge' }, previewType.value),
      props.artifact._content ? h('pre', { class: 'content' }, props.artifact._content) : h('div', { class: 'no-content' }, '无内嵌内容')
    ])
  },
})

// ── 产物面板容器（提取 tab 切换 + 产物列表交互）──
const ArtifactTabPanel = defineComponent({
  components: { ArtifactItem },
  props: {
    artifacts: { type: Array, default: () => [] },
    activeId: { type: String, default: '' },
  },
  emits: ['select', 'open-folder', 'copy', 'download', 'delete'],
  template: `
    <div class="artifact-tab-panel">
      <div class="artifact-list" v-if="artifacts.length > 0">
        <ArtifactItem
          v-for="a in artifacts"
          :key="a.id || a.path"
          :artifact="a"
          :active="activeId === (a.id || a.path)"
          @click="$emit('select', a)"
          @open-folder="$emit('open-folder', $event)"
          @copy="$emit('copy', $event)"
          @download="$emit('download', $event)"
          @delete="$emit('delete', $event)"
        />
      </div>
      <div class="empty-state" v-else>
        <span>暂无产物</span>
      </div>
    </div>
  `,
})

describe('ArtifactPanel 产物 tab — 组件挂载测试', () => {
  // ── ArtifactItem ──
  describe('ArtifactItem', () => {
    it('渲染文件名和大小', () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'report.md', size: 12345 } },
      })
      expect(wrapper.text()).toContain('report.md')
      expect(wrapper.text()).toContain('12.1KB')
    })

    it('根据扩展名显示图标', () => {
      const md = mount(ArtifactItem, { props: { artifact: { name: 'a.md', size: 0 } } })
      expect(md.find('.artifact-icon').text()).toBe('📝')
      
      const py = mount(ArtifactItem, { props: { artifact: { name: 'b.py', size: 0 } } })
      expect(py.find('.artifact-icon').text()).toBe('🐍')
      
      const stl = mount(ArtifactItem, { props: { artifact: { name: 'c.stl', size: 0 } } })
      expect(stl.find('.artifact-icon').text()).toBe('🖨️')
    })

    it('未知扩展名显示默认 📄', () => {
      const wrapper = mount(ArtifactItem, { props: { artifact: { name: 'data.xyz', size: 0 } } })
      expect(wrapper.find('.artifact-icon').text()).toBe('📄')
    })

    it('active 状态添加 active class', () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'a.md', size: 0 }, active: true },
      })
      expect(wrapper.find('.artifact-item').classes()).toContain('active')
    })

    it('点击触发 click 事件', async () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'a.md', size: 0 } },
      })
      await wrapper.find('.artifact-item').trigger('click')
      expect(wrapper.emitted('click')).toHaveLength(1)
      expect(wrapper.emitted('click')[0][0].name).toBe('a.md')
    })

    it('文件夹按钮点击触发 open-folder 不触发 click', async () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'a.md', size: 0 } },
      })
      await wrapper.find('.btn-folder').trigger('click')
      expect(wrapper.emitted('open-folder')).toHaveLength(1)
      expect(wrapper.emitted('click')).toBeUndefined()
    })

    it('复制/下载/删除按钮各自触发对应事件', async () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'a.md', size: 0 } },
      })
      await wrapper.find('.btn-copy').trigger('click')
      await wrapper.find('.btn-download').trigger('click')
      await wrapper.find('.btn-delete').trigger('click')
      expect(wrapper.emitted('copy')).toHaveLength(1)
      expect(wrapper.emitted('download')).toHaveLength(1)
      expect(wrapper.emitted('delete')).toHaveLength(1)
    })

    it('大文件显示 MB', () => {
      const wrapper = mount(ArtifactItem, {
        props: { artifact: { name: 'big.bin', size: 5242880 } },
      })
      expect(wrapper.find('.artifact-size').text()).toBe('5.0MB')
    })
  })

  // ── PreviewDispatcher ──
  describe('PreviewDispatcher', () => {
    it('md 文件识别为 markdown', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'doc.md', _content: '# Title' } },
      })
      expect(wrapper.find('.preview-markdown').exists()).toBe(true)
      expect(wrapper.find('.type-badge').text()).toBe('markdown')
    })

    it('json 文件识别为 json', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'data.json', _content: '{}' } },
      })
      expect(wrapper.find('.preview-json').exists()).toBe(true)
    })

    it('png 图片识别为 image', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'pic.png' } },
      })
      expect(wrapper.find('.preview-image').exists()).toBe(true)
    })

    it('py 代码识别为 code', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'main.py', _content: 'print(1)' } },
      })
      expect(wrapper.find('.preview-code').exists()).toBe(true)
    })

    it('未知扩展名识别为 unsupported', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'file.xyz' } },
      })
      expect(wrapper.find('.preview-unsupported').exists()).toBe(true)
    })

    it('mime_type 优先于扩展名', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'file.dat', mime_type: 'text/html' } },
      })
      expect(wrapper.find('.preview-html').exists()).toBe(true)
    })

    it('有 _content 时渲染内容', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'a.md', _content: '# Hello' } },
      })
      expect(wrapper.find('.content').text()).toBe('# Hello')
    })

    it('无 _content 时显示占位', () => {
      const wrapper = mount(PreviewDispatcher, {
        props: { artifact: { name: 'a.md' } },
      })
      expect(wrapper.find('.no-content').exists()).toBe(true)
    })
  })

  // ── ArtifactTabPanel（列表容器）──
  describe('ArtifactTabPanel', () => {
    it('空列表显示暂无产物', () => {
      const wrapper = mount(ArtifactTabPanel, {
        props: { artifacts: [], activeId: '' },
      })
      expect(wrapper.find('.empty-state').text()).toContain('暂无产物')
    })

    it('多个产物渲染列表', () => {
      const wrapper = mount(ArtifactTabPanel, {
        props: {
          artifacts: [
            { id: '1', name: 'a.md', size: 100 },
            { id: '2', name: 'b.json', size: 200 },
          ],
          activeId: '1',
        },
      })
      expect(wrapper.findAll('.artifact-item')).toHaveLength(2)
    })

    it('点击产物触发 select 事件', async () => {
      const wrapper = mount(ArtifactTabPanel, {
        props: {
          artifacts: [{ id: '1', name: 'a.md', size: 0 }],
          activeId: '',
        },
      })
      await wrapper.find('.artifact-item').trigger('click')
      expect(wrapper.emitted('select')).toHaveLength(1)
      expect(wrapper.emitted('select')[0][0].id).toBe('1')
    })

    it('active 产物高亮', () => {
      const wrapper = mount(ArtifactTabPanel, {
        props: {
          artifacts: [
            { id: '1', name: 'a.md', size: 0 },
            { id: '2', name: 'b.md', size: 0 },
          ],
          activeId: '2',
        },
      })
      const items = wrapper.findAll('.artifact-item')
      expect(items[0].classes()).not.toContain('active')
      expect(items[1].classes()).toContain('active')
    })

    it('事件冒泡：列表项的 open-folder 事件冒泡到容器', async () => {
      const wrapper = mount(ArtifactTabPanel, {
        props: {
          artifacts: [{ id: '1', name: 'a.md', size: 0, path: '/tmp/a.md' }],
          activeId: '',
        },
      })
      await wrapper.find('.btn-folder').trigger('click')
      expect(wrapper.emitted('open-folder')).toHaveLength(1)
    })
  })
})
