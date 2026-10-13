/**
 * `v-perm` 指令：按当前身份收敛界面入口（呈现层，不是安全措施）。
 *
 * 用法：
 *   v-perm="'risk.decide'"                无权限 → 禁用（保留位置，用户知道有这么个功能）
 *   v-perm="['task.retry','task.batch']"  数组 = 任一命中即可
 *   v-perm.hide="'rule.edit'"             无权限 → 直接移除（图标按钮类，禁用会很突兀）
 *
 * ⚠️ 这里踩过一个很隐蔽的坑，改动前务必读完：
 *
 *   v-perm 的入参基本都是**字面量**（`'rule.edit'`），也就是说 binding.value
 *   永远等于 binding.oldValue。原先的 updated 钩子写成：
 *
 *       el.classList.remove('perm-denied')          // 先清掉上次的禁用痕迹
 *       if (binding.value !== binding.oldValue) bind(el, binding)   // 再按需重算
 *
 *   结果是：**清了，但永远不会再算**。触发链路是
 *     mounted 打上禁用 → 表格数据回来触发一次 updated → 禁用痕迹被清掉 →
 *     值没变所以不重算 → 元素恢复成可用状态。
 *   于是「法务审查人」在规则页能直接点开「编辑条件」抽屉（实测确实能开），
 *   而 title 里还留着「无此权限」的提示，看着像生效了其实没生效。
 *   切换身份更明显：admin 切到 lawyer 后按钮依然是活的。
 *
 *   修法有两层，缺一不可：
 *     1. updated 里**无条件重算**，不做 value 比较；
 *     2. 额外 watch 认证 store 的 roleKey —— 身份变化时元素自身的响应式依赖
 *        可能根本没变（vnode 不重渲染就没有 updated 钩子），只能靠外部信号兜底。
 */
import { watch } from 'vue'

import { useAuthStore } from '../stores/auth'
import { permLabel } from '../permissions'

/** 所有打过 v-perm 的元素的重算回调；身份一变全部重跑 */
const registry = new Set()
let stopAuthWatch = null

function startAuthWatch() {
  if (stopAuthWatch) return
  const auth = useAuthStore()
  stopAuthWatch = watch(
    () => auth.roleKey,
    () => registry.forEach((fn) => fn()),
    { flush: 'post' }
  )
}

function stopAuthWatchIfIdle() {
  if (registry.size === 0 && stopAuthWatch) {
    stopAuthWatch()
    stopAuthWatch = null
  }
}

/** 清掉上一次施加的禁用/隐藏痕迹，回到「元素本来该有的样子」 */
function reset(el) {
  if (el.__permBlocker) {
    el.removeEventListener('click', el.__permBlocker, true)
    el.__permBlocker = null
  }
  el.classList.remove('perm-denied')
  el.removeAttribute('disabled')
  el.removeAttribute('aria-disabled')

  // title 要还原成元素原本的值，别把组件自己的 tooltip 吃掉
  if (!Object.prototype.hasOwnProperty.call(el, '__permTitle0')) {
    el.__permTitle0 = el.getAttribute('title') // 首次进入时记住原始值（可能是 null）
  }
  if (el.__permTitle0) el.setAttribute('title', el.__permTitle0)
  else el.removeAttribute('title')

  if (el.__permHidden) {
    el.style.display = ''
    el.__permHidden = false
  }
}

function apply(el, binding) {
  reset(el)

  const auth = useAuthStore()
  const wanted = (Array.isArray(binding.value) ? binding.value : [binding.value]).filter(Boolean)
  if (auth.hasAny(wanted)) return

  if (binding.modifiers.hide) {
    el.__permHidden = true
    el.style.display = 'none'
    return
  }

  el.classList.add('perm-denied')
  el.setAttribute('disabled', 'disabled')
  el.setAttribute('aria-disabled', 'true')
  el.setAttribute('title', `当前身份「${auth.role.name}」无此权限：${wanted.map(permLabel).join(' / ')}`)

  // capture 阶段拦截，防止冒泡到卡片 / 表格行触发跳转
  el.__permBlocker = (e) => {
    e.stopPropagation()
    e.preventDefault()
  }
  el.addEventListener('click', el.__permBlocker, true)
}

export const permDirective = {
  mounted(el, binding) {
    // binding 存在 holder 里而不是闭包里，这样 updated 换入参时不用重建注册项
    el.__permHolder = { binding }
    el.__permRun = () => apply(el, el.__permHolder.binding)
    registry.add(el.__permRun)
    startAuthWatch()
    el.__permRun()
  },

  updated(el, binding) {
    if (!el.__permRun) return
    el.__permHolder.binding = binding
    el.__permRun() // 无条件重算 —— 别再加 value !== oldValue 判断
  },

  unmounted(el) {
    if (el.__permRun) {
      registry.delete(el.__permRun)
      el.__permRun = null
      stopAuthWatchIfIdle()
    }
    reset(el)
  },
}

export default permDirective
