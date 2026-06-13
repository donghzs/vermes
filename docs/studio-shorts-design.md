# Studio AI 短剧 — UI & 流水线方案

## 参考竞品（已完成调研）

| 产品 | 借鉴点 | 可复制模式 |
|------|--------|-----------|
| **即梦AI** | Agent模式 / 技能系统 / 故事板 / 完整创作链路 | agent驱动，分镜→生成→剪辑→配音→导出 |
| **可灵AI** | 角色一致性（角色保持），运动笔刷 | 参考图注入+一致性检测 |
| **Runway Gen-3** | 精细运镜控制，时间线编辑，多模态输入 | Camera Control面板，多层时间线 |
| **Pika 2.0** | 局部编辑，场景检测 | 局部重绘UI |
| **剪映AI (CapCut)** | 自动字幕，AI配音，智能剪辑，模板 | 字幕引擎，TTS集成 |
| **ComfyUI** | 节点式工作流编排 | 节点可视化+数据流 |
| **Morph Studio** | 故事板系统 | 分镜可视化UI |

---

## 一、整体架构：短剧创作流水线

短剧创作是一条从构思到出片的完整链路。核心架构分三层：

```
创作层 (Frontend)                  编排层 (Pipeline)              生成层 (API)
┌──────────────────┐           ┌──────────────────┐          ┌──────────────────┐
│  剧本编辑         │           │   Pipeline        │          │  Agnes API       │
│  故事板/分镜      │  ─────→  │   ①分镜生成        │  ─────→ │  ├ 文生视频        │
│  角色管理         │           │   ②角色设定        │         │  ├ 图生视频        │
│  时间线           │           │   ③逐镜生成        │         │  ├ 多图视频        │
│  配音/字幕        │           │   ④配音合成        │         │  └ 关键帧          │
│  预览/导出        │           │   ⑤字幕对齐        │         │                  │
│                   │           │   ⑥拼接导出        │         │  TTS API         │
│                   │           └──────────────────┘         │  ├ Azure TTS      │
│                   │                                        │  └ 火山 TTS       │
└──────────────────┘                                        └──────────────────┘
                                    ↑
                            流水线引擎（后端自研）
```

---

## 二、短剧流水线设计

### 2.1 Pipeline 定义

```python
# 短剧流水线步骤类型
PIPELINE_STEPS: dict = {
    'write_script':     'AI 生成/编辑剧本',
    'storyboard':       '剧本→分镜拆分',
    'create_character': '创建角色（上传/生成参考图）',
    'generate_shot':    '生成单镜头视频',
    'lip_sync':         '口型同步',
    'tts':              'AI 配音',
    'subtitle':         '自动字幕',
    'compose':          '多镜头拼接/剪辑',
    'transition':       '添加转场/特效',
    'export':           '导出成品',
}
```

### 2.2 预置短剧流水线

```
┌─────────────────────────────────────────────────────┐
│ 🎬 标准短剧创作流水线                                │
│                                                     │
│ ① 写剧本 → ② 拆分镜 → ③ 创建角色 → ④ 逐镜生成       │
│ → ⑤ AI配音 → ⑥ 自动字幕 → ⑦ 拼接剪辑 → ⑧ 导出      │
│                                                     │
│ 🎬 图转动画流水线                                    │
│                                                     │
│ ① 上传概念图 → ② 设定角色 → ③ 写旁白 → ④ 逐镜头      │
│ → ⑤ 配音 → ⑥ 字幕 → ⑦ 拼接 → ⑧ 导出               │
│                                                     │
│ 🎬 模板短剧流水线                                    │
│                                                     │
│ ① 选模板(甜宠/悬疑/逆袭) → ② 填角色 → ③ 自动剧本     │
│ → ④ 自动分镜 → ⑤ 一键生成 → ⑥ 导出                 │
└─────────────────────────────────────────────────────┘
```

### 2.3 流水线步骤详情

```python
class StoryboardStep:
    """剧本→分镜转换"""
    
    def execute(self, ctx):
        script = ctx.get('script')
        # LLM 解析剧本→分镜列表
        shots = self._parse_script(script)
        # 每个分镜包含：镜头描述、角色、场景、运镜、对白
        ctx.set('shots', shots)
        return shots

class GenerateShotStep:
    """生成单个镜头视频"""
    
    def execute(self, ctx):
        shot = ctx.current_shot
        # 注入角色参考图
        images = []
        for char in shot.characters:
            if char.ref_image:
                images.append(char.ref_image)
        
        # 构建 prompt（含运镜描述）
        prompt = f"{shot.scene_description}, {shot.camera_movement}"
        
        # 调用生成 API
        if len(images) == 0:
            result = video_api.text_to_video(prompt)
        elif len(images) == 1:
            result = video_api.image_to_video(prompt, images[0])
        else:
            result = video_api.multi_image_to_video(prompt, images)
        
        ctx.set_shot_video(shot.id, result)
        return result
```

---

## 三、UI 设计

### 3.1 主界面布局

```
┌────────────────────────────────────────────────────────┐
│ Studio  ▐  电商设计  ▐  AI短剧  (导航栏)               │
├─────────┬───────────────────────────────┬─────────────┤
│         │                               │              │
│ 流水线   │      主工作区                   │ 属性面板    │
│ 面板     │                               │              │
│         │                               │ 当前镜头     │
│ [流水线] │  [故事板]  [时间线]  [预览]    │             │
│         │                               │ 角色: xxx   │
│ ① 剧本   │  ┌──────┐  ┌──────┐         │ 场景: xxx   │
│ ② 分镜   │  │镜头1 │  │镜头2 │         │ 运镜: xxx   │
│ ③ 角色   │  │      │  │      │         │ 对白: ...   │
│ ④ 生成   │  │[vid] │  │[vid] │         │             │
│ ⑤ 配音   │  └──────┘  └──────┘         │ [🔊 配音]  │
│ ⑥ 字幕   │  ┌──────┐  ┌──────┐         │ [📝 字幕]  │
│ ⑦ 拼接   │  │镜头3 │  │镜头4 │         │             │
│ ⑧ 导出   │  │      │  │      │         │ [▶ 生成]   │
│         │  │      │  │      │         │             │
│ [▶ 全部] │  └──────┘  └──────┘         │             │
└─────────┴───────────────────────────────┴─────────────┘
│  底部：播放器 / 时间线 / 输出预览                       │
└────────────────────────────────────────────────────────┘
```

### 3.2 故事板（Storyboard）

```
┌───────────────────────────────────────────────────┐
│  故事板                              [+ 添加镜头]  │
├───────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ 镜头 1     │ 镜头 2     │ 镜头 3     │          │
│  │ 📷 远景    │ 📷 特写    │ 📷 中景    │          │
│  │ ┌──────┐ │ ┌──────┐ │ ┌──────┐ │          │
│  │ │      │ │ │      │ │ │      │ │          │
│  │ │ img  │ │ │ img  │ │ │ img  │ │          │
│  │ │      │ │ │      │ │ │      │ │          │
│  │ └──────┘ │ └──────┘ │ └──────┘ │          │
│  │ 暮色街道   │ 主角表情   │ 对话场景   │          │
│  │ 推镜头     │ 摇镜头     │ 固定镜头   │          │
│  │ "我终于   │ "你...    │ "我走了"  │          │
│  │  找到你"  │           │           │          │
│  │ [▶生成]  │ [▶生成]  │ [▶生成]  │          │
│  └──────────┘ └──────────┘ └──────────┘          │
└───────────────────────────────────────────────────┘
```

**故事板卡片数据结构**：
```json
{
  "id": "shot_001",
  "number": 1,
  "description": "女主角站在暮色街道上",
  "scene_type": "远景",
  "camera": "推镜头, 慢速推进",
  "dialogue": "我终于找到你了",
  "characters": ["女主"],
  "reference_image": null,
  "style": "电影感, 暖色调",
  "duration_seconds": 5,
  "video_url": null,
  "status": "pending"
}
```

### 3.3 剧本编辑

```
┌─────────────────────────────────────────────────┐
│  剧本                              [AI 写剧本]   │
├─────────────────────────────────────────────────┤
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │ 场景1: 暮色街道·傍晚                     │    │
│  │                                         │    │
│  │ 【远景】女主角站在昏黄的路灯下，远处       │    │
│  │ 车流灯光模糊成光轨。镜头缓缓推进。         │    │
│  │                                         │    │
│  │ 女主（轻声）: "我终于找到你了。"         │    │
│  │                                         │    │
│  │ ...                                     │    │
│  └─────────────────────────────────────────┘    │
│                                                  │
│  [📐 自动拆分分镜]    [📋 选择短剧模板]           │
└─────────────────────────────────────────────────┘
```

**拆分逻辑**：LLM 按「场景切换 + 景别变化 + 对白段落」三个信号自动分割剧本 → 映射到故事板卡片。

### 3.4 角色管理

```
┌────────────────────────────────────────────┐
│  角色库                          [+ 添加]  │
├────────────────────────────────────────────┤
│  ┌────────┐  ┌────────┐  ┌────────┐      │
│  │ 女主    │  │ 男主    │  │ 配角    │      │
│  │ ┌────┐ │  │ ┌────┐ │  │ ┌────┐ │      │
│  │ │img │ │  │ │img │ │  │ │img │ │      │
│  │ └────┘ │  │ └────┘ │  │ └────┘ │      │
│  │ 小美    │  │ 阿杰    │  │ 路人甲  │      │
│  │ 温柔   │  │ 阳光   │  │        │      │
│  │ [✏️]  │  │ [✏️]  │  │ [✏️]  │      │
│  └────────┘  └────────┘  └────────┘      │
└────────────────────────────────────────────┘
```

**角色数据结构**：
```json
{
  "id": "char_001",
  "name": "小美",
  "description": "27岁, 温柔坚韧, 长发",
  "ref_images": ["data:image/..."],
  "style": "电影感",
  "voice": "温柔女声",
  "used_in_shots": ["shot_001", "shot_003"]
}
```

### 3.5 镜头控制面板

```
┌────────────────────────────────────┐
│  运镜控制                           │
├────────────────────────────────────┤
│                                     │
│  景别: [ 远景 | 中景 | 特写 ]       │
│                                     │
│  运镜:                             │
│  [固定] [推] [拉] [摇] [移] [跟]   │
│                                     │
│  速度: [慢速 ●○○○○ 快速]           │
│                                     │
│  角度: [平视] [俯拍] [仰拍]         │
│                                     │
│  路径:                             │
│  [○ 直线] [○ 弧线] [○ Z字形]       │
│                                     │
│  ▶ 预览运镜动画                     │
└────────────────────────────────────┘
```

### 3.6 时间线

```
┌────────────────────────────────────────────────────────────┐
│  时间线                                       总长: 1:23   │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐                 │
│  │ 镜头1││ 镜头2││ 镜头3││ 镜头4││ 镜头5│                 │
│  │ 0:05 ││ 0:08 ││ 0:12 ││ 0:15 ││ 0:20 │                 │
│  └──────┘└──────┘└──────┘└──────┘└──────┘                 │
│                                                             │
│  🔊 音轨: [===配音===] [===BGM===] [===音效===]             │
│  📝 字幕: [.........字幕..........]                         │
│                                                             │
│  [✂️ 剪切] [🔗 转场] [🎬 预览]                             │
└────────────────────────────────────────────────────────────┘
```

---

## 四、后端对接方案

### 4.1 API 端点

```python
# 短剧相关端点
router = APIRouter(prefix="/api/studio/shorts", tags=["studio-shorts"])

# 剧本相关
POST /api/studio/shorts/script/generate    # AI 生成剧本
POST /api/studio/shorts/script/parse       # 剧本→分镜拆分

# 角色管理
POST /api/studio/shorts/characters         # 创建角色
GET  /api/studio/shorts/characters         # 角色列表
POST /api/studio/shorts/characters/upload  # 上传角色参考图

# 短剧生成
POST /api/studio/shorts/generate/shot      # 生成单镜头
POST /api/studio/shorts/generate/batch     # 批量生成（流水线）

# 后期
POST /api/studio/shorts/tts                # AI 配音
POST /api/studio/shorts/subtitle           # 自动字幕
POST /api/studio/shorts/compose            # 拼接成片
POST /api/studio/shorts/export             # 导出

# 预置
GET  /api/studio/shorts/templates          # 短剧模板列表
```

### 4.2 功能实现矩阵

| 功能 | 实现方式 | 依赖 | 难度 |
|------|---------|------|:----:|
| **剧本→分镜** | LLM (agnes-2.0-flash) 解析 | Agent API | ⭐ |
| **角色一致性 v1** | 用户上传参考图 → 注入到每段生成 prompt | 前端+后端 | ⭐ |
| **角色一致性 v2** | 角色嵌入向量库 + 跨镜头一致性检测 | 自研 | ⭐⭐⭐⭐⭐ |
| **单镜头生成** | 调 Agnes 视频 API（文本/图片/多图） | Agnes API | ⭐ |
| **运镜控制 prompt级** | prompt 自然语言描述 | Agnes API | ⭐ |
| **运镜控制 UI面板** | 前端参数→转 prompt | 前端 | ⭐⭐ |
| **AI 配音 (TTS)** | 接入 Azure TTS / 火山引擎 TTS | 第三方 API | ⭐ |
| **自动字幕** | Whisper 语音识别 + 字幕渲染 | openai-whisper | ⭐⭐ |
| **口型同步** | Wav2Lip 开源方案 | 自部署 | ⭐⭐⭐⭐⭐ |
| **多镜头拼接** | FFmpeg 后端拼接 | FFmpeg | ⭐ |
| **转场/特效** | FFmpeg filters + AI特效 | FFmpeg | ⭐⭐⭐ |
| **智能剪辑** | 场景检测+节奏分析 | 自研 | ⭐⭐⭐⭐ |
| **短剧模板** | 预置剧本+分镜+角色模板 | 数据驱动 | ⭐⭐ |

### 4.3 角色一致性方案

```
方案A（短期·快速上线）：
  用户上传角色参考图 → 存入角色库
  每个镜头生成时，自动将角色图作为 image 参数传入
  优点：纯API调用，零自研  缺点：多镜头间角色可能略有变化

方案B（中期·合理质量）：
  方案A + 后处理一致性检测
  用 CLIP / DINO 提取角色特征向量 → 跨镜头比对
  异常镜头自动重新生成
  优点：可检测不一致  缺点：重生成增加耗时

方案C（长期·专业质量）：
  自研角色注入 LoRA / Adapter
  每个角色训练一个轻量级 LoRA → 推理时动态加载
  优点：角色100%统一  缺点：需要训练管线
```

---

## 五、开源可参考的项目

| 项目 | 用途 | 集成方式 |
|------|------|---------|
| **Wav2Lip** | 口型同步 | Python 后端部署 |
| **Whisper (OpenAI)** | 语音识别→字幕 | pip install openai-whisper |
| **FFmpeg** | 视频拼接/转场/剪辑 | 命令行调用 |
| **MoviePy** | Python 视频编辑 | pip install moviepy |
| **Coqui TTS** | 开源 TTS | Python 后端 |
| **VLLM** | 视频 LLM 分析 | 可选 |
| **Fabric.js** | 故事板 Canvas | 前端 npm |
| **Timeline Vue** | 时间线组件 | 前端自研 |

**推荐快速集成方案**：

```
字幕: Whisper → FFmpeg drawtext 渲染
配音: Azure TTS (免费层够用)
口型同步: Wav2Lip (自部署，不需要训练)
拼接: FFmpeg concat demuxer
转场: FFmpeg xfade / gltransition
```

---

## 六、流水线引擎统一设计

电商和短剧共用同一个 Pipeline Engine，只是步骤不同。

```
Pipeline Engine (通用)
    ├── Step 接口
    ├── 上下文管理 PipelineContext
    ├── 状态存储 (SQLite/Redis)
    └── 异步执行器 (ThreadPoolExecutor)
    
电商 Pipeline:                       短剧 Pipeline:
  RemoveBgStep                         StoryboardStep
  ReplaceBgStep                        CreateCharacterStep
  ApplyTemplateStep                    GenerateShotStep
  BatchExportStep                      TTSStep
                                       SubtitleStep
                                       ComposeStep
```

### 核心接口

```python
class BaseStep(ABC):
    """流水线步骤基类"""
    
    @abstractmethod
    def execute(self, ctx: PipelineContext) -> Any:
        """执行步骤"""
        pass
    
    @property
    def name(self) -> str:
        """步骤名"""
        return self.__class__.__name__


class PipelineContext:
    """流水线上下文：存储中间结果"""
    
    def __init__(self, params: dict):
        self.params = params          # 原始参数
        self.storage: dict = {}       # 步骤间共享数据
        self.outputs: list = []       # 输出列表
    
    def set(self, key, value):
        self.storage[key] = value
    
    def get(self, key, default=None):
        return self.storage.get(key, default)
    
    def add_output(self, output: dict):
        self.outputs.append(output)


class PipelineRunner:
    """流水线执行器"""
    
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._tasks: dict[str, PipelineTask] = {}
    
    def run(self, pipeline_name: str, params: dict) -> str:
        task_id = str(uuid.uuid4())
        task = PipelineTask(id=task_id, pipeline_name=pipeline_name, params=params)
        self._tasks[task_id] = task
        self._executor.submit(self._execute, task)
        return task_id
    
    def status(self, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if not task:
            return {"error": "not found"}
        return task.to_dict()
    
    def _execute(self, task: PipelineTask):
        steps = PIPELINE_REGISTRY[task.pipeline_name]
        ctx = PipelineContext(task.params)
        
        for i, step_cls in enumerate(steps):
            step = step_cls()
            task.current_step = i
            task.step_name = step.name
            task.status = 'running'
            
            try:
                result = step.execute(ctx)
                task.step_results.append(result)
            except Exception as e:
                task.status = 'failed'
                task.error = str(e)
                return
        
        task.status = 'completed'
        task.result = ctx.outputs
```

---

## 七、实施路线

### Phase 1（1周）：MVP 链路

1. **剧本→分镜→生成→拼接→导出** 核心链路
2. 剧本编辑 UI + 分镜自动化（LLM 解析）
3. 故事板网格视图
4. 单镜头生成（调现有视频 API）
5. FFmpeg 拼接成片

### Phase 2（2周）：角色与配音

6. 角色管理 UI（上传参考图 + 跨镜头注入）
7. AI 配音（Azure TTS 集成）
8. 自动字幕（Whisper + 字幕渲染）
9. 镜头控制面板（UI参数→prompt 转换）
10. 时间线初步（镜头拖拽排序）

### Phase 3（1月）：专业级功能

11. 口型同步（Wav2Lip 集成）
12. 转场/特效库
13. 短剧模板（甜宠/悬疑/逆袭预置）
14. 一致性检测 + 自动重生成
15. 批量生成（多短剧并行）

---

## 八、与即梦AI的差异化策略

| 维度 | 即梦AI | 我们的优势 |
|------|--------|-----------|
| 模型能力 | Seedance 2.0 独家 | 多厂商切换：Agnes/可灵/Runway/Pika |
| 角色一致性 | 有 | 方案A即可追平，方案B+C可超越 |
| 流水线 | Agent模式（自动） | Agent + 手动流水线编排（灵活度更高） |
| 后期 | 轻度 | 可集成剪映式剪辑能力 |
| 模板 | 有 | 可做社区模板市场 |
| 开源 | 闭源 | 可做开源吸引社区贡献 |
| 分发 | 即梦App | Vermes 桌面端+微信小程序 |

**核心差异化**：不做封闭平台，做**多模型适配器 + 流水线编排器**——用户可以选择最佳模型生成每个镜头，而不是绑定一家。
