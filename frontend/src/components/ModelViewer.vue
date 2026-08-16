<script setup>
// ModelViewer Pro — 专业级 3D 模型查看器
// Three.js 通过 Vite 打包到本地 bundle，无 CDN 依赖

import * as THREE from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { ref, onMounted, watch, onBeforeUnmount } from 'vue'

const props = defineProps({
  src: { type: String, default: '' },
  autoRotate: { type: Boolean, default: false },
  transparentBg: { type: Boolean, default: true },
  viewMode: { type: String, default: 'perspective' },
  tool: { type: String, default: 'none' },
  sectionPlane: { type: String, default: 'none' },
  wireframe: { type: Boolean, default: false },
})

const emit = defineEmits(['measure', 'pick', 'loaded', 'bbox'])

const loading = ref(true)
const error = ref('')
const containerRef = ref(null)
const hoverFace = ref(null)

let scene = null
let camera = null
let renderer = null
let controls = null
let mesh = null
let animationId = null
let raycaster = null
let mouse = { x: 0, y: 0 }
let highlightMesh = null
let hoverHighlight = null
let measurePoints = []
let measureLines = []
let markers = []
let resizeObs = null
let bboxHelper = null

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
  renderer.localClippingEnabled = true
  container.innerHTML = ''
  container.appendChild(renderer.domElement)

  scene.add(new THREE.AmbientLight(0xffffff, 0.5))
  const d1 = new THREE.DirectionalLight(0xffffff, 0.8)
  d1.position.set(1, 1, 1)
  scene.add(d1)
  const d2 = new THREE.DirectionalLight(0xffffff, 0.3)
  d2.position.set(-1, -1, -1)
  scene.add(d2)

  const grid = new THREE.GridHelper(200, 20, 0x444444, 0x222222)
  grid.position.y = -0.1
  scene.add(grid)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.rotateSpeed = 0.8
  controls.zoomSpeed = 1.0
  controls.panSpeed = 0.8
  controls.minDistance = 10
  controls.maxDistance = 500

  raycaster = new THREE.Raycaster()

  renderer.domElement.addEventListener('click', onCanvasClick)
  renderer.domElement.addEventListener('mousemove', onCanvasMove)

  resizeObs = new ResizeObserver(() => {
    if (!renderer || !container) return
    const w = container.clientWidth
    const h = container.clientHeight
    if (w > 0 && h > 0) {
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
  })
  resizeObs.observe(container)
}

function loadSTL(url) {
  const loader = new STLLoader()
  return new Promise((resolve, reject) => {
    loader.load(url, (geometry) => {
      geometry.computeVertexNormals()
      const material = new THREE.MeshPhongMaterial({
        color: 0x22c55e,
        specular: 0x111111,
        shininess: 50,
        wireframe: props.wireframe,
        flatShading: false,
        side: THREE.DoubleSide,
      })
      mesh = new THREE.Mesh(geometry, material)

      const box = new THREE.Box3().setFromObject(mesh)
      const center = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3())
      const maxDim = Math.max(size.x, size.y, size.z)
      const scale = 80 / (maxDim || 1)
      mesh.scale.setScalar(scale)
      mesh.position.sub(center.multiplyScalar(scale))

      mesh.userData.originalSize = size
      mesh.userData.scale = scale

      scene.add(mesh)
      loading.value = false

      emit('bbox', {
        length_mm: round(size.x),
        width_mm: round(size.y),
        height_mm: round(size.z),
        volume_mm3: round(size.x * size.y * size.z),
      })
      emit('loaded', { size })

      if (bboxHelper) scene.remove(bboxHelper)
      const boxGeom = new THREE.BoxGeometry(size.x * scale, size.y * scale, size.z * scale)
      const boxEdges = new THREE.EdgesGeometry(boxGeom)
      bboxHelper = new THREE.LineSegments(boxEdges, new THREE.LineBasicMaterial({ color: 0x666666, transparent: true, opacity: 0.3 }))
      scene.add(bboxHelper)

      setViewMode(props.viewMode)
      resolve(mesh)
    }, undefined, (err) => reject(err))
  })
}

function round(v) { return Math.round(v * 100) / 100 }

function setViewMode(mode) {
  if (!camera || !controls) return
  const d = 100
  switch (mode) {
    case 'front': camera.position.set(0, 0, d); camera.up.set(0, 1, 0); break
    case 'top': camera.position.set(0, d, 0); camera.up.set(0, 0, -1); break
    case 'side': camera.position.set(d, 0, 0); camera.up.set(0, 1, 0); break
    case 'iso': camera.position.set(d * 0.6, d * 0.6, d * 0.6); camera.up.set(0, 1, 0); break
    default: camera.position.set(60, 60, 100); camera.up.set(0, 1, 0)
  }
  controls.target.set(0, 0, 0)
  controls.update()
}

function onCanvasMove(event) {
  if (!mesh || !raycaster) return
  const rect = renderer.domElement.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

  if (props.tool === 'pick' || props.tool === 'measure') {
    raycaster.setFromCamera(mouse, camera)
    const hits = raycaster.intersectObject(mesh)
    if (hits.length > 0) {
      if (!hoverHighlight) {
        hoverHighlight = new THREE.Mesh(
          new THREE.BufferGeometry(),
          new THREE.MeshBasicMaterial({ color: 0x88ddff, side: THREE.DoubleSide, transparent: true, opacity: 0.3 })
        )
        scene.add(hoverHighlight)
      }
      const face = hits[0].face
      const pos = mesh.geometry.attributes.position
      const v1 = new THREE.Vector3().fromBufferAttribute(pos, face.a).applyMatrix4(mesh.matrixWorld)
      const v2 = new THREE.Vector3().fromBufferAttribute(pos, face.b).applyMatrix4(mesh.matrixWorld)
      const v3 = new THREE.Vector3().fromBufferAttribute(pos, face.c).applyMatrix4(mesh.matrixWorld)
      hoverHighlight.geometry.setFromPoints([v1, v2, v3])
      hoverHighlight.geometry.setIndex([0, 1, 2])
      hoverHighlight.geometry.computeVertexNormals()
      hoverFace.value = { point: hits[0].point, normal: face.normal }
    } else if (hoverHighlight) {
      hoverHighlight.geometry = new THREE.BufferGeometry()
    }
  } else if (hoverHighlight) {
    hoverHighlight.geometry = new THREE.BufferGeometry()
  }
}

function onCanvasClick(event) {
  if (!mesh || !raycaster || props.tool === 'none') return
  const rect = renderer.domElement.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObject(mesh)
  if (intersects.length === 0) return
  const hit = intersects[0]
  const point = hit.point.clone()

  if (props.tool === 'measure') {
    measurePoints.push(point)
    addMarker(point)
    if (measurePoints.length >= 2) {
      const p1 = measurePoints[0], p2 = measurePoints[1]
      const dist = p1.distanceTo(p2)
      const scale = mesh.userData.scale || 1
      emit('measure', { distance_mm: round(dist / scale), p1, p2 })
      drawMeasureLine(p1, p2)
      measurePoints = []
    }
  } else if (props.tool === 'pick') {
    if (highlightMesh) scene.remove(highlightMesh)
    const face = hit.face
    const pos = mesh.geometry.attributes.position
    const v1 = new THREE.Vector3().fromBufferAttribute(pos, face.a).applyMatrix4(mesh.matrixWorld)
    const v2 = new THREE.Vector3().fromBufferAttribute(pos, face.b).applyMatrix4(mesh.matrixWorld)
    const v3 = new THREE.Vector3().fromBufferAttribute(pos, face.c).applyMatrix4(mesh.matrixWorld)
    const faceGeom = new THREE.BufferGeometry()
    faceGeom.setFromPoints([v1, v2, v3])
    faceGeom.setIndex([0, 1, 2])
    faceGeom.computeVertexNormals()
    highlightMesh = new THREE.Mesh(faceGeom, new THREE.MeshBasicMaterial({ color: 0xff8800, side: THREE.DoubleSide, transparent: true, opacity: 0.6 }))
    scene.add(highlightMesh)
    const screen = { x: event.clientX - rect.left, y: event.clientY - rect.top }
    emit('pick', { point: screen, face: hit.face, normal: hit.face.normal, worldPoint: hit.point })
  }
}

function addMarker(point) {
  const geom = new THREE.SphereGeometry(1.5, 16, 16)
  const mat = new THREE.MeshBasicMaterial({ color: 0xff4444 })
  const marker = new THREE.Mesh(geom, mat)
  marker.position.copy(point)
  scene.add(marker)
  markers.push(marker)
}

function drawMeasureLine(p1, p2) {
  const geom = new THREE.BufferGeometry().setFromPoints([p1, p2])
  const mat = new THREE.LineBasicMaterial({ color: 0xffff00, linewidth: 2 })
  const line = new THREE.Line(geom, mat)
  scene.add(line)
  measureLines.push(line)
}

function clearMeasure() {
  measureLines.forEach(l => scene.remove(l))
  markers.forEach(m => scene.remove(m))
  measureLines = []
  markers = []
  measurePoints = []
}

function applySection(plane) {
  if (!renderer) return
  if (plane === 'none') {
    if (mesh) mesh.material.clippingPlanes = []
    return
  }
  const p = new THREE.Plane()
  switch (plane) {
    case 'xy': p.set(new THREE.Vector3(0, 1, 0), 0); break
    case 'yz': p.set(new THREE.Vector3(1, 0, 0), 0); break
    case 'xz': p.set(new THREE.Vector3(0, 0, 1), 0); break
  }
  if (mesh) mesh.material.clippingPlanes = [p]
}

function animate() {
  animationId = requestAnimationFrame(animate)
  if (controls) controls.update()
  if (props.autoRotate && mesh) mesh.rotation.y += 0.005
  if (renderer && scene && camera) renderer.render(scene, camera)
}

onMounted(async () => {
  if (!props.src) {
    error.value = '未指定文件'
    loading.value = false
    return
  }
  const ext = props.src.split('.').pop().toLowerCase()
  if (!['stl', 'step', 'stp', 'svg', 'png', 'jpg', 'jpeg', 'glb', 'gltf'].includes(ext)) {
    error.value = `暂不支持 .${ext} 格式`
    loading.value = false
    return
  }
  if (!['stl', 'step', 'stp', 'glb', 'gltf'].includes(ext)) {
    loading.value = false
    return
  }
  try {
    initScene(containerRef.value)
    if (ext === 'stl') {
      await loadSTL(props.src)
    } else if (ext === 'step' || ext === 'stp') {
      // 先尝试直接加载同名 STL（后端可能已转换）
      const stlUrl = props.src.replace(/\.(step|stp)$/i, '.stl')
      try {
        await loadSTL(stlUrl)
      } catch {
        // 同名 STL 不存在，调后端 tessellate
        try {
          const res = await fetch(props.src + '/tessellate')
          const data = await res.json()
          if (data.ok && data.stl_url) {
            await loadSTL(data.stl_url)
          } else {
            error.value = data.error || 'STEP 转 STL 失败'
            loading.value = false
          }
        } catch {
          error.value = 'STEP 预览需后端转换，请确保引擎已安装'
          loading.value = false
        }
      }
    }
    animate()
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

watch(() => props.src, async (newSrc) => {
  if (!newSrc || !scene) return
  loading.value = true
  error.value = ''
  if (mesh) { scene.remove(mesh); mesh = null }
  if (highlightMesh) { scene.remove(highlightMesh); highlightMesh = null }
  if (hoverHighlight) { scene.remove(hoverHighlight); hoverHighlight = null }
  if (bboxHelper) { scene.remove(bboxHelper); bboxHelper = null }
  clearMeasure()
  try {
    const ext = newSrc.split('.').pop().toLowerCase()
    if (ext === 'stl') await loadSTL(newSrc)
    else if (ext === 'step' || ext === 'stp') {
      const stlUrl = newSrc.replace(/\.(step|stp)$/i, '.stl')
      try {
        await loadSTL(stlUrl)
      } catch {
        const res = await fetch(newSrc + '/tessellate')
        const data = await res.json()
        if (data.ok && data.stl_url) {
          await loadSTL(data.stl_url)
        } else {
          error.value = data.error || 'STEP 转 STL 失败'
        }
      }
    }
  } catch (e) {
    error.value = e.message || '加载失败'
    loading.value = false
  }
})

watch(() => props.viewMode, (mode) => setViewMode(mode))
watch(() => props.wireframe, (wf) => { if (mesh) mesh.material.wireframe = wf })
watch(() => props.sectionPlane, (plane) => applySection(plane))

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId)
  if (renderer) renderer.dispose()
  if (resizeObs) resizeObs.disconnect()
  if (renderer?.domElement) {
    renderer.domElement.removeEventListener('click', onCanvasClick)
    renderer.domElement.removeEventListener('mousemove', onCanvasMove)
  }
})

defineExpose({ clearMeasure })
</script>

<template>
  <div class="relative w-full h-full rounded-lg overflow-hidden" :class="transparentBg ? 'bg-transparent' : 'bg-gray-900'">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center z-10">
      <div class="text-center">
        <div class="inline-block w-8 h-8 border-3 border-green-500 border-t-transparent rounded-full animate-spin mb-2"></div>
        <p class="text-sm text-gray-500">加载 3D 模型中…</p>
      </div>
    </div>
    <div v-if="error" class="absolute inset-0 flex items-center justify-center z-10">
      <div class="text-center p-4">
        <p class="text-red-500 text-sm">⚠️ {{ error }}</p>
      </div>
    </div>
    <div ref="containerRef" class="w-full h-full" style="min-height: 400px"></div>
  </div>
</template>
