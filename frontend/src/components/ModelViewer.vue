<script setup>
// ModelViewer Pro — 专业级 3D 模型查看器
//
// 能力：
// - STL/STEP 渲染（Three.js CDN 按需加载）
// - 多视图切换（透视/前/上/侧/等轴）
// - 测量工具（点两个面/点显示距离）
// - 剖视图（按 XY/YZ/XZ 平面切开）
// - 点击拾取（Raycaster 选面/边/顶点高亮）
// - 自动旋转/线框/背景切换

import { ref, computed, onMounted, watch, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  src: { type: String, default: '' },
  autoRotate: { type: Boolean, default: false },
  transparentBg: { type: Boolean, default: true },
  // 视图模式: perspective / front / top / side / iso
  viewMode: { type: String, default: 'perspective' },
  // 工具模式: none / measure / section / pick
  tool: { type: String, default: 'none' },
  // 剖切平面: xy / yz / xz
  sectionPlane: { type: String, default: 'xy' },
  // 线框模式
  wireframe: { type: Boolean, default: false },
})

const emit = defineEmits(['measure', 'pick', 'loaded'])

const loading = ref(true)
const error = ref('')
const containerRef = ref(null)

// Three.js 实例（非响应式）
let THREE = null
let scene = null
let camera = null
let renderer = null
let controls = null
let mesh = null
let animationId = null
let raycaster = null
let mouse = null
let highlightMesh = null
let measurePoints = []
let measureLines = []
let sectionMesh = null

// ── Three.js 动态加载 ──

function ensureThreeJS() {
  return new Promise((resolve, reject) => {
    if (window.THREE) {
      THREE = window.THREE
      resolve()
      return
    }
    const script = document.createElement('script')
    script.type = 'module'
    script.textContent = `
      import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';
      import { STLLoader } from 'https://unpkg.com/three@0.160.0/examples/jsm/loaders/STLLoader.js';
      import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';
      window.THREE = THREE;
      window.STLLoader = STLLoader;
      window.OrbitControls = OrbitControls;
    `
    script.onload = () => {
      THREE = window.THREE
      resolve()
    }
    script.onerror = () => reject(new Error('Three.js CDN 加载失败'))
    document.head.appendChild(script)
  })
}

// ── 初始化场景 ──

function initScene(container) {
  const width = container.clientWidth || 600
  const height = container.clientHeight || 400

  scene = new THREE.Scene()
  if (!props.transparentBg) {
    scene.background = new THREE.Color(0x1a1a2e)
  }

  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000)
  camera.position.set(60, 60, 100)

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: props.transparentBg })
  renderer.setSize(width, height)
  renderer.setPixelRatio(window.devicePixelRatio)
  container.innerHTML = ''
  container.appendChild(renderer.domElement)

  // 光照
  const ambient = new THREE.AmbientLight(0xffffff, 0.5)
  scene.add(ambient)
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
  dirLight.position.set(1, 1, 1)
  scene.add(dirLight)
  const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.3)
  dirLight2.position.set(-1, -1, -1)
  scene.add(dirLight2)

  // 网格地面
  const gridHelper = new THREE.GridHelper(200, 20, 0x444444, 0x222222)
  gridHelper.position.y = -0.1
  scene.add(gridHelper)

  // 控制器
  const OrbitControls = window.OrbitControls
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.05

  // Raycaster
  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  // 点击事件
  renderer.domElement.addEventListener('click', onCanvasClick)

  // 窗口大小调整
  const resizeObserver = new ResizeObserver(() => {
    if (!renderer || !container) return
    const w = container.clientWidth
    const h = container.clientHeight
    if (w > 0 && h > 0) {
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
  })
  resizeObserver.observe(container)

  return resizeObserver
}

let resizeObs = null

// ── 加载 STL ──

async function loadSTL(url) {
  const STLLoader = window.STLLoader
  const loader = new STLLoader()

  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (geometry) => {
        geometry.computeVertexNormals()
        const material = new THREE.MeshPhongMaterial({
          color: 0x22c55e,
          specular: 0x111111,
          shininess: 50,
          wireframe: props.wireframe,
          flatShading: false,
        })
        mesh = new THREE.Mesh(geometry, material)

        // 自动居中 + 缩放到标准大小
        const box = new THREE.Box3().setFromObject(mesh)
        const center = box.getCenter(new THREE.Vector3())
        const size = box.getSize(new THREE.Vector3())
        const maxDim = Math.max(size.x, size.y, size.z)
        const scale = 80 / (maxDim || 1)
        mesh.scale.setScalar(scale)
        mesh.position.sub(center.multiplyScalar(scale))

        // 存储原始尺寸用于测量显示
        mesh.userData.originalSize = size
        mesh.userData.scale = scale

        scene.add(mesh)
        loading.value = false
        emit('loaded', { size })

        // 设置初始视角
        setViewMode(props.viewMode)

        resolve(mesh)
      },
      undefined,
      (err) => reject(err)
    )
  })
}

// ── 视图模式切换 ──

function setViewMode(mode) {
  if (!camera || !controls) return
  const d = 100
  switch (mode) {
    case 'front':
      camera.position.set(0, 0, d)
      camera.up.set(0, 1, 0)
      break
    case 'top':
      camera.position.set(0, d, 0)
      camera.up.set(0, 0, -1)
      break
    case 'side':
      camera.position.set(d, 0, 0)
      camera.up.set(0, 1, 0)
      break
    case 'iso':
      camera.position.set(d * 0.6, d * 0.6, d * 0.6)
      camera.up.set(0, 1, 0)
      break
    case 'perspective':
    default:
      camera.position.set(60, 60, 100)
      camera.up.set(0, 1, 0)
  }
  controls.target.set(0, 0, 0)
  controls.update()
}

// ── 点击拾取 ──

function onCanvasClick(event) {
  if (!mesh || !raycaster || props.tool === 'none') return

  const rect = renderer.domElement.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObject(mesh)

  if (intersects.length > 0) {
    const hit = intersects[0]
    const point = hit.point.clone()

    if (props.tool === 'measure') {
      measurePoints.push(point)
      addMarker(point)

      if (measurePoints.length >= 2) {
        const p1 = measurePoints[0]
        const p2 = measurePoints[1]
        const distance = p1.distanceTo(p2)
        // 转换为原始尺寸
        const scale = mesh.userData.scale || 1
        const realDistance = distance / scale
        emit('measure', { distance: realDistance, p1, p2 })
        drawMeasureLine(p1, p2)
        measurePoints = []
      }
    } else if (props.tool === 'pick') {
      // 高亮面
      if (highlightMesh) scene.remove(highlightMesh)
      const faceGeom = new THREE.BufferGeometry()
      const face = hit.face
      const positions = mesh.geometry.attributes.position
      const v1 = new THREE.Vector3().fromBufferAttribute(positions, face.a).applyMatrix4(mesh.matrixWorld)
      const v2 = new THREE.Vector3().fromBufferAttribute(positions, face.b).applyMatrix4(mesh.matrixWorld)
      const v3 = new THREE.Vector3().fromBufferAttribute(positions, face.c).applyMatrix4(mesh.matrixWorld)
      faceGeom.setFromPoints([v1, v2, v3])
      faceGeom.setIndex([0, 1, 2])
      faceGeom.computeVertexNormals()
      const faceMat = new THREE.MeshBasicMaterial({
        color: 0xff8800,
        side: 2,
        transparent: true,
        opacity: 0.5,
      })
      highlightMesh = new THREE.Mesh(faceGeom, faceMat)
      scene.add(highlightMesh)
      emit('pick', { point, face: hit.face, normal: hit.face.normal })
    }
  }
}

// ── 标记点 ──

function addMarker(point) {
  const geom = new THREE.SphereGeometry(1.5, 16, 16)
  const mat = new THREE.MeshBasicMaterial({ color: 0xff4444 })
  const marker = new THREE.Mesh(geom, mat)
  marker.position.copy(point)
  scene.add(marker)
}

// ── 测量线 ──

function drawMeasureLine(p1, p2) {
  const geom = new THREE.BufferGeometry().setFromPoints([p1, p2])
  const mat = new THREE.LineBasicMaterial({ color: 0xffff00, linewidth: 2 })
  const line = new THREE.Line(geom, mat)
  scene.add(line)
  measureLines.push(line)
}

// ── 清除测量 ──

function clearMeasure() {
  measureLines.forEach(l => scene.remove(l))
  measureLines = []
  measurePoints = []
  // 清除标记球
  scene.children.forEach(child => {
    if (child.geometry && child.geometry.type === 'SphereGeometry') {
      scene.remove(child)
    }
  })
}

// ── 剖视图 ──

function applySection(plane) {
  if (!renderer) return
  if (sectionMesh) {
    scene.remove(sectionMesh)
    sectionMesh = null
  }
  if (plane === 'none') return

  // 用 clipping plane
  const localPlane = new THREE.Plane()
  switch (plane) {
    case 'xy':
      localPlane.set(new THREE.Vector3(0, 1, 0), 0)
      break
    case 'yz':
      localPlane.set(new THREE.Vector3(1, 0, 0), 0)
      break
    case 'xz':
      localPlane.set(new THREE.Vector3(0, 0, 1), 0)
      break
  }
  renderer.localClippingEnabled = true
  if (mesh) {
    mesh.material.clippingPlanes = [localPlane]
    mesh.material.clipShadows = true
  }
}

// ── 渲染循环 ──

function animate() {
  animationId = requestAnimationFrame(animate)
  if (controls) controls.update()
  if (props.autoRotate && mesh) {
    mesh.rotation.y += 0.005
  }
  if (renderer && scene && camera) {
    renderer.render(scene, camera)
  }
}

// ── 生命周期 ──

onMounted(async () => {
  if (!props.src) {
    error.value = '未指定文件'
    loading.value = false
    return
  }

  const ext = props.src.split('.').pop().toLowerCase()
  if (!['stl', 'step', 'stp'].includes(ext)) {
    error.value = `暂不支持 .${ext} 格式预览，支持 STL/STEP`
    loading.value = false
    return
  }

  try {
    await ensureThreeJS()
    resizeObs = initScene(containerRef.value)

    if (ext === 'stl') {
      await loadSTL(props.src)
    } else if (ext === 'step' || ext === 'stp') {
      // STEP 需后端转 STL 后加载
      const stlUrl = props.src.replace(/\.(step|stp)$/i, '.stl')
      try {
        await loadSTL(stlUrl)
      } catch {
        error.value = 'STEP 预览需后端转换 STL，暂不可用'
        loading.value = false
      }
    }

    animate()
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

watch(() => props.src, async (newSrc) => {
  if (!newSrc || !THREE) return
  loading.value = true
  error.value = ''
  // 清旧 mesh
  if (mesh) {
    scene.remove(mesh)
    mesh = null
  }
  if (highlightMesh) {
    scene.remove(highlightMesh)
    highlightMesh = null
  }
  clearMeasure()
  try {
    const ext = newSrc.split('.').pop().toLowerCase()
    if (ext === 'stl') {
      await loadSTL(newSrc)
    } else if (ext === 'step' || ext === 'stp') {
      const stlUrl = newSrc.replace(/\.(step|stp)$/i, '.stl')
      await loadSTL(stlUrl)
    }
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

watch(() => props.viewMode, (mode) => {
  setViewMode(mode)
})

watch(() => props.wireframe, (wf) => {
  if (mesh) mesh.material.wireframe = wf
})

watch(() => props.transparentBg, (tp) => {
  if (scene) {
    scene.background = tp ? null : new THREE.Color(0x1a1a2e)
  }
})

watch(() => props.sectionPlane, (plane) => {
  applySection(plane)
})

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId)
  if (renderer) renderer.dispose()
  if (resizeObs) resizeObs.disconnect()
  if (renderer && renderer.domElement) {
    renderer.domElement.removeEventListener('click', onCanvasClick)
  }
})

defineExpose({ clearMeasure })
</script>

<template>
  <div class="relative w-full h-full rounded-lg overflow-hidden" :class="transparentBg ? 'bg-transparent' : 'bg-gray-900'">
    <!-- Loading -->
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center z-10">
      <div class="text-center">
        <div class="inline-block w-8 h-8 border-3 border-green-500 border-t-transparent rounded-full animate-spin mb-2"></div>
        <p class="text-sm text-gray-500">加载 3D 模型中…</p>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="absolute inset-0 flex items-center justify-center z-10">
      <div class="text-center p-4">
        <p class="text-red-500 text-sm">⚠️ {{ error }}</p>
      </div>
    </div>

    <!-- Three.js 容器 -->
    <div ref="containerRef" class="w-full h-full" style="min-height: 400px"></div>
  </div>
</template>
