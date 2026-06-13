# Studio 电商设计 — UI & 流水线方案

## 参考竞品（已完成调研）

| 产品 | 借鉴点 | 可复制模式 |
|------|--------|-----------|
| **Canva** | Magic Resize（多尺寸），Magic Eraser（扩图/重绘），模板系统 | 布局自适应算法，AI编辑功能 |
| **稿定设计** | 智能抠图，电商模板（行业分类体系），批量套版 | 模板DSL+字段绑定机制 |
| **图怪兽** | 一键多尺寸，批量操作UI | 轻量级批量工作流 |
| **京东智造** | 数据驱动批量出图，商品图合成管线 | 数据→模板→渲染的pipeline |
| **RemoveBG** | 一键抠图交互 | 后端SAM+前端refine |
| **ComfyUI** | 节点式工作流 | 功能流水线的编排概念 |

---

## 一、整体架构：流水线工作台

放弃当前 Studio 的「左侧配置+右侧输出」布局，改为 **流水线工作台** 模式：

```
┌─────────────────────────────────────────────────────┐
│ Studio ── 电商设计 ── AI短剧                        │  ← 顶部导航
├──────────┬──────────┬──────────┬────────────────────┤
│          │          │          │                    │
│  工具    │  画布    │  属性    │  流水线面板        │
│  面板    │  (中间)  │  面板    │  (右侧/底部)       │
│          │          │          │                    │
│  抠图     │          │  尺寸     │  Pipeline:          │
│  裁剪     │          │  模型     │  ① 导入图片         │
│  文字     │          │  样式     │  ② 智能抠图         │
│  模板     │          │  参数     │  ③ 换背景: 白色    │
│  滤镜     │          │          │  ④ 套模板: 促销    │
│  批处理   │          │          │  ⑤ 导出: 3尺寸     │
│          │          │          │  [▶ 执行流水线]     │
├──────────┴──────────┴──────────┴────────────────────┤
│  底部：输出区 / 历史记录 / 批量任务队列               │
└─────────────────────────────────────────────────────┘
```

### 为什么这样设计？

- **即梦AI** 走 Agent 模式 → 自然语言驱动流水线
- **Canva** 走编辑器模式 → 拖拽操作
- **ComfyUI** 走节点模式 → 可视化编排

我们的方案：**三种模式合一**，用户按需选择。

---

## 二、流水线（Pipeline）系统设计

### 2.1 Pipeline 定义

```
Pipeline = [Step1, Step2, Step3, ...]

Step = {
  type: 'remove_bg' | 'replace_bg' | 'apply_template' | 
        'resize' | 'add_text' | 'auto_enhance' | 'add_watermark' |
        'generate_image' | 'image2image' | 'batch_export',
  params: { ... },
  status: 'pending' | 'running' | 'done' | 'failed'
}
```

### 2.2 预置流水线（电商场景）

```
┌──────────────────────────────────────────────┐
│ 📦 商品主图流水线                             │
│                                              │
│ ① 上传商品图 → ② 智能抠图 → ③ 换白色背景     │
│ → ④ 套促销模板 → ⑤ 加logo水印 → ⑥ 导出3尺寸  │
│                                              │
│ 📦 套装模板批量出图                           │
│                                              │
│ ① 导入CSV(100个SKU) → ② 自动映射字段          │
│ → ③ 套指定模板 → ④ 批量渲染 → ⑤ 导出ZIP       │
│                                              │
│ 📦 商品图美化                                 │
│                                              │
│ ① 上传商品图 → ② 智能抠图 → ③ 选场景模板       │
│ → ④ 自动调色+阴影 → ⑤ 加文案 → ⑥ 导出         │
└──────────────────────────────────────────────┘
```

### 2.3 流水线执行引擎（后端）

```python
class PipelineEngine:
    """流水线执行引擎"""
    
    PIPELINE_REGISTRY = {
        'product_main': ProductMainPipeline,
        'bulk_template': BulkTemplatePipeline,
        'product_beauty': ProductBeautyPipeline,
    }
    
    def execute(self, pipeline_name: str, params: dict) -> str:
        """执行流水线，返回 task_id"""
        pipeline_cls = self.PIPELINE_REGISTRY[pipeline_name]
        pipeline = pipeline_cls(params)
        return self._run_async(pipeline)
    
    def get_status(self, task_id: str) -> dict:
        """查询流水线进度"""
        return self._status_store.get(task_id)
```

```python
class ProductMainPipeline(BasePipeline):
    """商品主图流水线"""
    
    steps = [
        RemoveBgStep,       # 智能抠图
        ReplaceBgStep,      # 换背景（参数: 白色/场景/渐变）
        ApplyTemplateStep,  # 套模板
        AddWatermarkStep,   # 加水印/logo
        BatchExportStep,    # 多尺寸导出
    ]
```

---

## 三、电商设计核心功能 UI

### 3.1 智能抠图

```
┌─────────────────────┐
│  智能抠图             │
│                     │
│  [上传图片]  区域      │
│                     │
│  ┌─────────────┐    │
│  │  原图   结果  │    │
│  │  [img] [img] │    │
│  └─────────────┘    │
│                     │
│  边缘优化: [滑块]     │
│  [保留] [羽化] [平滑] │
│                     │
│  [✓ 应用] [批量抠图]  │
└─────────────────────┘
```

**交互流程**：
1. 拖拽/上传图片 → 自动抠图（加载中动画）
2. 显示原图 vs 结果对比视图（左右/上下）
3. 边缘优化：羽化半径、边缘平滑度滑块
4. 「应用」→ 结果保存到画布
5. 「批量抠图」→ 弹窗选择多图 → 批量处理 → 下载ZIP

### 3.2 模板系统

```
┌──────────────────────────────────────────────┐
│  模板浏览器                             搜索  │
├──────┬───────────────────────────────────────┤
│ 分类  │  模板网格                             │
│       │                                       │
│ 全部  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐        │
│ 促销  │  │    │ │    │ │    │ │    │        │
│ 上新  │  │ T1 │ │ T2 │ │ T3 │ │ T4 │        │
│ 节日  │  └────┘ └────┘ └────┘ └────┘        │
│ 食品  │                                       │
│ 美妆  │  [自定义模板]  [我的模板]              │
│ 数码  │                                       │
└──────┴───────────────────────────────────────┘
```

**模板数据结构（DSL）**：
```json
{
  "name": "618促销主图",
  "category": "促销",
  "width": 800,
  "height": 800,
  "layers": [
    {
      "type": "background",
      "color": "#FF4444",
      "editable": false
    },
    {
      "type": "image",
      "name": "商品图",
      "x": 50, "y": 80, "w": 700, "h": 500,
      "fit": "contain",
      "editable": true,
      "bind_field": "product_image"  
    },
    {
      "type": "text",
      "name": "促销价",
      "x": 50, "y": 620, "w": 700, "h": 60,
      "font_size": 48,
      "color": "#FFD700",
      "bold": true,
      "text": "¥{price}",
      "editable": true,
      "bind_field": "price"
    },
    {
      "type": "shape",
      "name": "标签",
      "x": 680, "y": 20, "w": 100, "h": 40,
      "shape": "rounded_rect",
      "color": "#FF6600",
      "text": "爆款",
      "font_size": 16,
      "color": "#FFFFFF"
    }
  ]
}
```

### 3.3 批量套版

```
┌──────────────────────────────────────────────────────┐
│  批量套版                                  新任务     │
├──────────────────────────────────────────────────────┤
│  选择模板: [ 618促销主图 v ]                           │
│                                                      │
│  数据导入:                                           │
│  ┌──────────────────────────────────────────────┐   │
│  │ [📁 上传CSV] 或 [✏️ 手动输入]                │   │
│  │                                               │   │
│  │  SKU  | 商品图  | 价格   | 标题              │   │
│  │  A001 | img1.jpg | ¥99  | 超值套餐           │   │
│  │  A002 | img2.jpg | ¥199 | 豪华版             │   │
│  │  ... (共36条)                                │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  字段映射:  [商品图] → [模板.商品图]  ✓              │
│            [价格]   → [模板.促销价]  ✓              │
│                                                      │
│  导出设置:  [💾 导出全部]  [📦 压缩为ZIP]            │
│  尺寸:  ☑ 主图800x800  ☑ 直通车  ☑ 详情页宽图      │
│                                                      │
│  [▶ 开始生成]                                        │
│                                                      │
│  进度: ■■■■■■■■■□ 32/36                             │
└──────────────────────────────────────────────────────┘
```

### 3.4 多尺寸一键生成

```
┌──────────────────────────────────────┐
│  多尺寸生成                            │
├──────────────────────────────────────┤
│  支持的尺寸:                           │
│  ☑ 主图 800×800                      │
│  ☑ 直通车 800×800                    │
│  ☑ 详情页宽图 750×10000              │
│  ☑ 首页轮播 1920×600                 │
│  ☑ 小红书 1080×1440                  │
│  ☑ 抖音 1080×1920                    │
│  ☐ 自定义: [__] × [__]              │
│                                       │
│  自适应模式:                           │
│  ○ 智能裁剪（保持主体可见）            │
│  ○ 等比缩放（留白填充）               │
│  ○ 拉伸适配                           │
│                                       │
│  [▶ 生成全部尺寸]                     │
└──────────────────────────────────────┘
```

### 3.5 文字/文案排版

```
┌──────────────────────────────┐
│  文字工具                      │
├──────────────────────────────┤
│  文字: [¥99 限时抢购]         │
│                               │
│  字体: [思源黑体 v]           │
│  字号: [48] [B] [I] [U]      │
│  颜色: [■ 红色] [描边] [发光] │
│                               │
│  电商样式预设:                 │
│  [满减] [包邮] [新品] [爆款]  │
│  [限时] [买一送一] [第2件半价] │
│                               │
│  [✏️ 文案AI生成]              │
└──────────────────────────────┘
```

---

## 四、后端对接方案

### 4.1 API 端点设计

```python
# 新增端点（保持 Studio 独立路由）
router = APIRouter(prefix="/api/studio/v2", tags=["studio-v2"])

# 智能抠图
POST /api/studio/v2/remove-bg
  Request:  { image_data: "base64..." }
  Response: { mask: "base64...", result: "base64..." }

# 模板系统
GET  /api/studio/v2/templates                # 模板列表
GET  /api/studio/v2/templates/{id}           # 单个模板
POST /api/studio/v2/templates                # 创建模板
PUT  /api/studio/v2/templates/{id}           # 更新模板

# 流水线
POST /api/studio/v2/pipeline/run             # 执行流水线
GET  /api/studio/v2/pipeline/{task_id}       # 查询流水线状态
GET  /api/studio/v2/pipeline/presets         # 预置流水线列表

# 批量操作
POST /api/studio/v2/batch/preview            # 批量预览
POST /api/studio/v2/batch/export             # 批量导出

# 多尺寸
POST /api/studio/v2/resize                   # 单张多尺寸生成

# 文案生成
POST /api/studio/v2/copywriting              # 电商文案AI生成
```

### 4.2 功能实现方案

| 功能 | 实现方式 | 依赖 |
|------|---------|------|
| **智能抠图** | SAM2 (segment-anything-2) 本地推理 + 后处理 refine | backend 集成 |
| **背景替换** | 抠图结果 + Inpainting (Agnes API) 生成新背景 | Agnes API |
| **模板渲染** | 前端 Canvas (Fabric.js/Konva) 渲染模板图层 | 前端纯实现 |
| **批量套版** | 模板DSL + 字段绑定 + 后端 Pillow/Cairo 批量渲染 | 后端渲染引擎 |
| **多尺寸生成** | Canvas 布局自适应算法（锚点系统） | 前端+后端 |
| **AI调色** | 调用 Agnes API image edit，或自研LUT | Agnes API |
| **文案生成** | LLM API (agnes-2.0-flash) 生成电商文案 | Agent API |
| **图生图(换装)** | Agnes image2image + prompt控制 | Agnes API |
| **AI扩图** | Agnes 支持扩图指令或 outpainting API | Agnes API |

---

## 五、流水线嵌入方案（工程级）

### 5.1 核心技术：Pipeline 编排器

```python
@router.post("/pipeline/run")
def run_pipeline(req: PipelineRequest):
    """执行流水线"""
    pipeline_id = str(uuid.uuid4())
    
    # 1. 解析流水线步骤
    steps = parse_pipeline_steps(req.pipeline, req.params)
    
    # 2. 创建工作流任务
    task = WorkflowTask(
        id=pipeline_id,
        steps=steps,
        status='pending',
        created_at=datetime.now(),
    )
    _workflow_store[pipeline_id] = task
    
    # 3. 异步执行（后台线程）
    _executor.submit(_run_pipeline_async, pipeline_id, steps, req)
    
    return PipelineResponse(
        success=True,
        task_id=pipeline_id,
        total_steps=len(steps),
    )

def _run_pipeline_async(pipeline_id, steps, req):
    """异步执行流水线的每一步"""
    ctx = PipelineContext(req.params)
    
    for i, step in enumerate(steps):
        _workflow_store[pipeline_id].current_step = i
        _workflow_store[pipeline_id].status = f'step_{i}'
        
        try:
            result = step.execute(ctx)
            ctx.set_output(i, result)
        except Exception as e:
            _workflow_store[pipeline_id].status = 'failed'
            _workflow_store[pipeline_id].error = str(e)
            return
    
    _workflow_store[pipeline_id].status = 'completed'
    _workflow_store[pipeline_id].result = ctx.final_output()
```

### 5.2 前端流水线 UI

```vue
<!-- PipelineRunner.vue - 流水线执行器 -->
<template>
  <div class="pipeline-runner">
    <!-- 流水线步骤可视化 -->
    <div class="pipeline-steps">
      <div v-for="(step, i) in pipeline.steps" 
           :key="i" 
           class="step"
           :class="{ active: currentStep === i, done: step.status === 'done', failed: step.status === 'failed' }">
        <div class="step-icon">{{ step.icon }}</div>
        <div class="step-name">{{ step.name }}</div>
        <div class="step-connector" v-if="i < pipeline.steps.length - 1">→</div>
      </div>
    </div>
    
    <!-- 当前步骤参数面板 -->
    <div class="step-params">
      <component :is="stepComponent" :step="currentStepConfig" />
    </div>
    
    <!-- 执行控制 -->
    <div class="pipeline-controls">
      <button @click="runPipeline">▶ 执行流水线</button>
      <button @click="saveAsTemplate">💾 存为模板</button>
      <div class="progress" v-if="running">
        <div class="progress-bar" :style="{ width: progressPercent + '%' }"></div>
        <span>{{ currentStepName }} ({{ completedSteps }}/{{ totalSteps }})</span>
      </div>
    </div>
    
    <!-- 输出预览 -->
    <div class="pipeline-output" v-if="outputs.length">
      <div v-for="(out, i) in outputs" :key="i" class="output-item">
        <img :src="out.url" v-if="out.type === 'image'" />
        <video :src="out.url" controls v-if="out.type === 'video'" />
        <span class="output-label">{{ out.label }}</span>
      </div>
    </div>
  </div>
</template>
```

### 5.3 预置流水线（模板化）

```javascript
// 预置流水线定义（前端）
const PRESET_PIPELINES = {
  'product_main': {
    name: '商品主图',
    icon: '📦',
    description: '上传商品图→抠图→换背景→套模板→加水印→多尺寸导出',
    steps: [
      { type: 'upload_image', name: '上传商品图', icon: '📁', params: { maxFiles: 1 } },
      { type: 'remove_bg', name: '智能抠图', icon: '✂️', params: { auto: true } },
      { type: 'replace_bg', name: '换背景', icon: '🖼️', params: { bgType: 'color', color: '#FFFFFF' } },
      { type: 'apply_template', name: '套模板', icon: '📋', params: { templateId: '' } },
      { type: 'add_watermark', name: '加水印', icon: '©️', params: { position: 'bottom-right' } },
      { type: 'batch_export', name: '多尺寸导出', icon: '📤', params: { sizes: ['800x800', '800x800', '750x10000'] } },
    ]
  },
  'bulk_template': {
    name: '批量套版',
    icon: '📊',
    description: '导入CSV数据→字段映射→批量套模板→导出ZIP',
    steps: [
      { type: 'import_csv', name: '导入数据', icon: '📄', params: {} },
      { type: 'map_fields', name: '字段映射', icon: '🔗', params: {} },
      { type: 'select_template', name: '选择模板', icon: '📋', params: {} },
      { type: 'batch_generate', name: '批量生成', icon: '⚡', params: { concurrency: 4 } },
      { type: 'export_zip', name: '导出ZIP', icon: '📦', params: {} },
    ]
  },
  'product_beauty': {
    name: '商品图美化',
    icon: '✨',
    description: '抠图→选场景→调色→加阴影→加文案→导出',
    steps: [
      { type: 'upload_image', name: '上传商品', icon: '📁' },
      { type: 'remove_bg', name: '智能抠图', icon: '✂️' },
      { type: 'select_scene', name: '选场景', icon: '🏠', params: { category: '' } },
      { type: 'auto_enhance', name: '智能调色', icon: '🎨', params: { style: 'auto' } },
      { type: 'add_shadow', name: '加阴影/倒影', icon: '🌓', params: { shadowType: 'drop' } },
      { type: 'add_text', name: '加文案', icon: '📝', params: { presets: true } },
      { type: 'export', name: '导出', icon: '📤' },
    ]
  }
};
```

---

## 六、与当前 Studio 的兼容方案

当前 Studio（`/api/studio/generate`）的工作模式是单次请求→单次响应。流水线模式不需要替代它，而是在同级添加：

```
/api/studio/generate          ← 保持现有（单次生成）
/api/studio/v2/               ← 新增（流水线模式）
  ├── remove-bg
  ├── templates/
  ├── pipeline/run
  ├── pipeline/presets
  ├── batch/
  └── resize
```

前端路由：
```
/studio                       ← 当前版本（保留）
/studio/v2                    ← 新流水线工作台
/studio/v2/templates          ← 模板浏览器
/studio/v2/ecommerce          ← 电商设计专区
/studio/v2/shorts             ← AI短剧专区
```

这样现有功能不受影响，用户可自由切换。

---

## 七、开源可参考的项目

| 项目 | 用途 | 集成方式 |
|------|------|---------|
| **SAM2 (Meta)** | 智能抠图 | onnx 本地推理，或 API 封装 |
| **rembg** | 轻量级抠图 | pip install，可直接调用 |
| **Fabric.js** | Canvas 编辑器 | 前端 npm 包，模板渲染引擎 |
| **Konva.js** | Canvas 编辑器（React友好） | 前端 npm 包 |
| **Pillow / Cairo** | 后端批量渲染 | Python 后端 |
| **FFmpeg** | 视频合成 | 后端命令行 |
| **OpenCV** | 图像预处理 | Python 后端 |

---

## 八、实施路线

### Phase 1（1周）：MVP

1. **智能抠图 UI + 后端** — SAM2/rembg 集成，对比预览
2. **预置流水线 x1** — 「商品主图」流水线全流程打通
3. **模板系统 v1** — JSON DSL 定义+渲染+保存

### Phase 2（2周）：核心功能

4. **批量套版** — CSV 导入 + 字段映射 + 批量渲染
5. **多尺寸生成** — 预设尺寸 + 自适应算法
6. **文字/文案系统** — 电商样式库 + AI 文案生成
7. **更多流水线** — 商品美化、批量套版

### Phase 3（1月）：完善

8. **背景替换UI** — 圈选+Inpaint
9. **AI扩图/局部重绘** — Outpainting UI
10. **模板市场** — 社区模板分享
11. **数据驱动批量** — ERP/商品数据对接
