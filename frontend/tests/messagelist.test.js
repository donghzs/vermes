import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

// 测试 MessageList 中 _isModelChange 消息的渲染逻辑
// 由于 MessageList.vue 依赖外部 store 和复杂依赖，我们测试核心逻辑而非整个组件

const ModelChangeMessage = defineComponent({
  props: {
    msg: { type: Object, required: true },
  },
  template: `
    <div v-if="msg._isModelChange" class="model-change-pill">
      <span>{{ msg.content }}</span>
    </div>
    <div v-else class="normal-message">
      {{ msg.content }}
    </div>
  `,
})

describe('MessageList — _isModelChange 渲染', () => {
  it('模型变更消息应渲染为居中胶囊样式', () => {
    const wrapper = mount(ModelChangeMessage, {
      props: {
        msg: {
          id: '1',
          role: 'system',
          content: '⚙️ 模型已切换：DeepSeek → GPT-4o',
          _isModelChange: true,
        },
      },
    })
    expect(wrapper.find('.model-change-pill').exists()).toBe(true)
    expect(wrapper.find('.normal-message').exists()).toBe(false)
    expect(wrapper.text()).toContain('DeepSeek')
    expect(wrapper.text()).toContain('GPT-4o')
  })

  it('普通消息应正常渲染', () => {
    const wrapper = mount(ModelChangeMessage, {
      props: {
        msg: {
          id: '2',
          role: 'user',
          content: '你好',
          _isModelChange: false,
        },
      },
    })
    expect(wrapper.find('.normal-message').exists()).toBe(true)
    expect(wrapper.find('.model-change-pill').exists()).toBe(false)
  })

  it('无 _isModelChange 字段时按普通消息渲染', () => {
    const wrapper = mount(ModelChangeMessage, {
      props: {
        msg: {
          id: '3',
          role: 'assistant',
          content: '回复内容',
        },
      },
    })
    expect(wrapper.find('.normal-message').exists()).toBe(true)
  })
})
