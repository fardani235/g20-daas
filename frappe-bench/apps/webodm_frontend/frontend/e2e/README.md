# Browser checks

`model-viewer.e2e.mjs` drives the 3D viewer in headless Chrome against a running
stack: login, model load and framing, every toolbar / mouse / keyboard action,
error + retry, not-found, repeated load/leave cycles, mid-download abort, phone
viewport and container resize. It asserts through `window.__modelViewer.snapshot()`,
which the page exposes in dev builds only, so point it at a Vite dev server.

Playwright is not a project dependency; install it in a scratch directory
(`npm i playwright`) and point `E2E_PLAYWRIGHT_DIR` at that directory.

```sh
# 1. dev server proxied to your stack (adjust target/host); from frontend/
cat > vite.local.config.js <<'CFG'
import { mergeConfig } from 'vite'
import base from './vite.config.js'
const proxy = { target: 'https://your-site.local', changeOrigin: true, secure: false, headers: { Host: 'your-site.local' } }
export default mergeConfig(base, { server: { port: 8081, strictPort: true, proxy: { '/api': proxy, '/private': proxy, '/files': proxy } } })
CFG
npx vite --config vite.local.config.js &

# 2. run the checks
E2E_PASSWORD_FILE=../../../../secrets/admin_password.txt \
E2E_PROJECT='My Project' E2E_TASK=abc123 E2E_TASK_TITLE='My Project - Task 1' \
E2E_SHOTS=/tmp/viewer-shots E2E_CHROME=/usr/bin/google-chrome \
E2E_PLAYWRIGHT_DIR=/path/to/scratch node e2e/model-viewer.e2e.mjs
```

The task must be Completed with a `model`. Software WebGL is used automatically
(`--use-angle=swiftshader`); a 34 MB survey model reaches "ready" in roughly 12 s.
Exit code 0 means every check passed.
