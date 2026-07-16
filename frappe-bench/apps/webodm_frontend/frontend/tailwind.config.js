import frappeUIPreset from 'frappe-ui/tailwind'

export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
    './node_modules/frappe-ui/src/**/*.{vue,ts,tsx}',
  ],
  presets: [frappeUIPreset],
}
