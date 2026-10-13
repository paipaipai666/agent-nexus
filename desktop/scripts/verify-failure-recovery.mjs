import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const desktop = join(root, 'desktop')
const temporaryRoot = join(root, '.tmp_pytest')
mkdirSync(temporaryRoot, { recursive: true })
const output = mkdtempSync(join(temporaryRoot, 'recovery-proof-'))
const checks = []

function artifact(name) {
  const path = resolve(output, name)
  const within = relative(output, path)
  if (isAbsolute(within) || within.startsWith('..')) throw new Error('Invalid verification output path')
  return path
}

function run(name, command, arguments_, cwd = root) {
  process.stdout.write(`${name}\n`)
  const result = spawnSync('rtk', ['proxy', command, ...arguments_], { cwd, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })
  checks.push({ name, passed: result.status === 0, status: result.status })
  writeFileSync(artifact(`${checks.length}.log`), (result.stdout ?? '') + (result.stderr ?? ''))
  writeFileSync(artifact('verification.json'), JSON.stringify({ checks, browserIncluded: !process.argv.includes('--unit-only') }, null, 2))
  if (result.status !== 0) {
    process.stderr.write((result.stdout ?? '') + (result.stderr ?? ''))
    if (result.error) process.stderr.write(result.error.message + '\n')
    process.exit(result.status ?? 1)
  }
  if (result.stdout) process.stdout.write(result.stdout)
}

run('TypeScript', process.execPath, [join(desktop, 'node_modules/typescript/bin/tsc'), '--noEmit'], desktop)
run('No-comment gate', 'python', ['scripts/check_no_comments.py'])
run('Python lint', 'python', ['-m', 'ruff', 'check',
  'agentnexus/core/failure.py', 'agentnexus/services/turn.py', 'agentnexus/services/chat.py',
  'agentnexus/memory/versioned.py', 'agentnexus/observability/timeline.py',
  'agentnexus/server/routes/chat.py', 'agentnexus/server/routes/version.py',
  'tests/unit/test_failure_recovery.py', 'tests/unit/test_confirmation_recovery.py', 'scripts/run_recovery_tests.py',
])
run('Desktop tests', process.execPath, [join(desktop, 'node_modules/vitest/vitest.mjs'), 'run', '--silent', '--maxWorkers=4', '--reporter=json', `--outputFile=${artifact('desktop-tests.json')}`], desktop)
run('Backend recovery and existing contracts', 'python', ['scripts/run_recovery_tests.py',
  ...['failure_recovery', 'confirmation_recovery', 'versioned', 'checkpoint_head', 'turn_runtime', 'per_round_commit',
    'chat_service', 'ws_agent_stream', 'ws_disconnect_cancels', 'gui_event_mapping', 'session_switch',
    'execution_context', 'realtime_events', 'session_search', 'context_usage', 'subagent_timeline', 'reconnect_resume',
    'workspace_isolation', 'session_api_isolation'].map(name => `tests/unit/test_${name}.py`),
])
run('Renderer build', process.execPath, [join(desktop, 'node_modules/vite/bin/vite.js'), 'build', '--config', 'vite.e2e.config.ts', '--outDir', artifact('renderer')], desktop)
run('Electron bundle', process.execPath, [join(desktop, 'node_modules/esbuild/bin/esbuild'), 'electron/main.ts', '--bundle', '--platform=node', '--external:electron', `--outfile=${artifact('electron-main.cjs')}`], desktop)
if (!process.argv.includes('--unit-only')) {
  run('Browser acceptance', process.execPath, [join(desktop, 'node_modules/@playwright/test/cli.js'), 'test',
    'e2e/failure-recovery.spec.ts', 'e2e/daily-friction.spec.ts', 'e2e/desktop-startup.spec.ts', '--workers=1', '--reporter=dot', `--output=${artifact('browser')}`], desktop)
}
const desktopReport = JSON.parse(readFileSync(artifact('desktop-tests.json'), 'utf8'))
writeFileSync(artifact('verification.json'), JSON.stringify({ checks, desktopTests: desktopReport.numPassedTests, browserIncluded: !process.argv.includes('--unit-only') }, null, 2))
process.stdout.write(`Verification report: ${artifact('verification.json')}\n`)
