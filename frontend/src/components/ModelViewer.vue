<script setup>
// ModelViewer：3D 模型 WebGL 视图器（P2）。
//
// 零 npm 依赖策略：
// - GLB/GLTF：用 <model-viewer> Web Component（Google 维护，CDN ~100KB）
// - STL：用 Three.js CDN（仅 STL 时加载，~600KB）
// - PNG/JPG：直接 <img>
//
// 加载方式按文件扩展名自动选择，用户可拖拽旋转/缩放/切换背景。

import { ref, computed, onMounted, watch, onBeforeUnmount } from 'vue'

const props = defineProps({
  // 文件 URL（相对路径，如 /api/mfgcad/files/xxx/output.stl）
  src: { type: String, default: '' },
  // 文件类型（auto=按扩展名判断 / glb / stl / image）
  type: { type: String, default: 'auto' },
  // 自动旋转
  autoRotate: { type: Boolean, default: false },
  // 背景透明
  transparentBg: { type: Boolean, default: true },
})

const loading = ref(true)
const error = ref('')
const viewerType = ref('') // 'model-viewer' / 'three-stl' / 'image'
const modelViewerLoaded = ref(false)

const ext = computed(() => {
  if (!props.src) return ''
  return props.src.split('.').pop().toLowerCase()
})

const resolvedType = computed(() => {
  if (props.type !== 'auto') return props.type
  const e = ext.value
  if (['glb', 'gltf'].includes(e)) return 'glb'
  if (e === 'stl') return 'stl'
  if (['png', 'jpg', 'jpeg'].includes(e)) return 'image'
  return 'glb' // 默认尝试 model-viewer
})

// ── model-viewer Web Component 加载 ─────────────────────

let modelViewerScript = null

function ensureModelViewer() {
  return new Promise((resolve, reject) => {
    if (customElements.get('model-viewer')) {
      resolve()
      return
    }
    if (modelViewerScript) {
      modelViewerScript.onload = resolve
      modelViewerScript.onerror = reject
      return
    }
    modelViewerScript = document.createElement('script')
    modelViewerScript.type = 'module'
    modelViewerScript.src = 'https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js'
    modelViewerScript.onload = resolve
    modelViewerScript.onerror = () => reject(new Error('model-viewer CDN 加载失败'))
    document.head.appendChild(modelViewerScript)
  })
}

// ── Three.js STL 加载（仅 STL 时按需加载） ──────────────

let threeScript = null

function ensureThreeJS() {
  return new Promise((resolve, reject) => {
    if (window.THREE) {
      resolve()
      return
    }
    if (threeScript) {
      threeScript.onload = resolve
      threeScript.onerror = reject
      return
    }
    // 加载 Three.js + STLLoader
    threeScript = document.createElement('script')
    threeScript.type = 'module'
    threeScript.textContent = `
      import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';
      import { STLLoader } from 'https://unpkg.com/three@0.160.0/examples/jsm/loaders/STLLoader.js';
      import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';
      window.THREE = THREE;
      window.STLLoader = STLLoader;
      window.OrbitControls = OrbitControls;
    `
    threeScript.onload = resolve
    threeScript.onerror = () => reject(new Error('Three.js CDN 加载失败'))
    document.head.appendChild(threeScript)
  })
}

// ── STL 渲染 ────────────────────────────────────────────

let stlScene = null
let stlRenderer = null
let stlAnimationId = null

async function renderSTL(container) {
  await ensureThreeJS()

  const THREE = window.THREE
  const STLLoader = window.STLLoader
  const OrbitControls = window.OrbitControls

  // 清旧
  if (stlRenderer) {
    stlRenderer.dispose()
    stlRenderer = null
  }
  if (stlAnimationId) {
    cancelAnimationFrame(stlAnimationId)
    stlAnimationId = null
  }

  const width = container.clientWidth || 400
  const height = container.clientHeight || 300

  // 场景
  stlScene = new THREE.Scene()
  if (!props.transparentBg) {
    stlScene.background = new THREE.Color(0x1a1a2e)
  }

  // 相机
  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000)
  camera.position.set(0, 0, 100)

  // 渲染器
  stlRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: props.transparentBg })
  stlRenderer.setSize(width, height)
  stlRenderer.setPixelRatio(window.devicePixelRatio)
  container.innerHTML = ''
  container.appendChild(stlRenderer.domElement)

  // 光照
  const ambient = new THREE.AmbientLight(0xffffff, 0.6)
  stlScene.add(ambient)
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
  dirLight.position.set(1, 1, 1)
  stlScene.add(dirLight)

  // 控制器
  const controls = new OrbitControls(camera, stlRenderer.domElement)
  controls.enableDamping = true

  // 加载 STL
  const loader = new STLLoader()
  loader.load(
    props.src,
    (geometry) => {
      geometry.computeVertexNormals()
      const material = new THREE.MeshPhongMaterial({
        color: 0x22c55e,
        specular: 0x111111,
        shininess: 50,
      })
      const mesh = new THREE.Mesh(geometry, material)

      // 自动居中+缩放
      const box = new THREE.Box3().setFromObject(mesh)
      const center = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3())
      const maxDim = Math.max(size.x, size.y, size.z)
      const scale = 50 / maxDim
      mesh.scale.setScalar(scale)
      mesh.position.sub(center.multiplyScalar(scale))

      stlScene.add(mesh)
      loading.value = false

      // 渲染循环
      const animate = () => {
        stlAnimationId = requestAnimationFrame(animate)
        controls.update()
        if (props.autoRotate) {
          mesh.rotation.y += 0.005
        }
        stlRenderer.render(stlScene, camera)
      }
      animate()
    },
    undefined,
    (err) => {
      error.value = `STL 加载失败: ${err.message || err}`
      loading.value = false
    }
  )
}

// ── 生命周期 ────────────────────────────────────────────

const containerRef = ref(null)

onMounted(async () => {
  if (!props.src) {
    error.value = '未指定文件'
    loading.value = false
    return
  }

  viewerType.value = resolvedType.value

  try {
    if (viewerType.value === 'glb') {
      await ensureModelViewer()
      modelViewerLoaded.value = true
      loading.value = false
    } else if (viewerType.value === 'stl') {
      await renderSTL(containerRef.value)
    } else if (viewerType.value === 'image') {
      loading.value = false
    }
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

watch(() => props.src, async (newSrc) => {
  if (!newSrc) return
  loading.value = true
  error.value = ''
  viewerType.value = resolvedType.value
  try {
    if (viewerType.value === 'glb') {
      await ensureModelViewer()
      modelViewerLoaded.value = true
      loading.value = false
    } else if (viewerType.value === 'stl') {
      await renderSTL(containerRef.value)
    } else if (viewerType.value === 'image') {
      loading.value = false
    }
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

onBeforeUnmount(() => {
  if (stlAnimationId) cancelAnimationFrame(stlAnimationId)
  if (stlRenderer) stlRenderer.dispose()
})

const bgClass = computed(() => props.transparentBg ? 'bg-transparent' : 'bg-gray-900')
</script>

<template>
  <div class="model-viewer-wrapper relative w-full h-full rounded-lg overflow-hidden" :class="bgClass">
    <!-- Loading -->
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center z-10">
      <div class="text-center">
        <div class="inline-block w-8 h-8 border-3 border-green-500 border-t-transparent rounded-full animate-spin mb-2"></div>
        <p class="text-sm text-gray-500">加载 3D 模型中…</p>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="absolute inset-0 flex items-center justify-center">
      <div class="text-center p-4">
        <p class="text-red-500 text-sm">⚠️ {{ error }}</p>
      </div>
    </div>

    <!-- GLB via model-viewer -->
    <model-viewer
      v-if="viewerType === 'glb' && modelViewerLoaded && !loading"
      :src="src"
      :auto-rotate="autoRotate"
      camera-controls
      shadow-intensity="1"
      environment-image="neutral"
      :class="['w-full h-full', bgClass]"
      style="min-height: 300px"
    >
      <div slot="poster" class="absolute inset-0 flex items-center justify-center">
        <p class="text-gray-400 text-sm">准备加载…</p>
      </div>
    </model-viewer>

    <!-- STL via Three.js -->
    <div
      v-if="viewerType === 'stl'"
      ref="containerRef"
      class="w-full h-full stl-container"
      style="min-height: 300px"
    ></div>

    <!-- Image -->
    <img
      v-if="viewerType === 'image' && !loading"
      :src="src"
      class="w-full h-full object-contain"
      style="min-height: 300px"
    />
  </div>
</template>

<style scoped>
.model-viewer-wrapper {
  min-height: 300px;
}
model-viewer {
  width: 100%;
  height: 100%;
  min-height: 300px;
  display: block;
}
</style>
