import { ref } from 'vue'

const STORAGE_KEY = 'theme'
const themes = ['light', 'dark', 'system']

const stored = localStorage.getItem(STORAGE_KEY)
const current = ref(themes.includes(stored) ? stored : 'system')

let mql = null

function apply(theme) {
  const isDark = theme === 'dark' || (theme === 'system' && mql?.matches)
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light')
  document.documentElement.classList.toggle('dark', isDark)
}

function onSystemChange() {
  if (current.value === 'system') apply('system')
}

function init() {
  mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', onSystemChange)
  apply(current.value)
}

function cleanup() {
  mql?.removeEventListener('change', onSystemChange)
}

function setTheme(theme) {
  if (!themes.includes(theme)) return
  current.value = theme
  localStorage.setItem(STORAGE_KEY, theme)
  apply(theme)
}

function cycle() {
  const idx = themes.indexOf(current.value)
  setTheme(themes[(idx + 1) % themes.length])
}

export function useTheme() {
  return { current, setTheme, cycle, init, cleanup }
}
