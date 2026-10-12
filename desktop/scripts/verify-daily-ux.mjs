import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const desktop = join(root, 'desktop')
const temporaryRoot = join(root, '.tmp_pytest')
mkdirSync(temporaryRoot, { recursive: true })
const output = mkdtempSync(join(temporaryRoot, 'daily-ux-proof-'))
const checks = []

function verifiedOutput(name) {
  const target = resolve(output, name)
  const within = relative(output, target)
  if (isAbsolute(within) || within.startsWith('..')) throw new Error('Invalid verification output path')
  return target
}

function run(name, command, args, cwd = root) {
  process.stdout.write(`${name}\n`)
  const result = spawnSync('rtk', ['proxy', command, ...args], { cwd, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })
  const passed = result.status === 0
  checks.push({ name, passed, status: result.status })
  if (!passed) {
    process.stderr.write((result.stdout ?? '') + (result.stderr ?? ''))
    if (result.error) process.stderr.write(result.error.message + '\n')
    writeFileSync(verifiedOutput('verification.json'), JSON.stringify({ checks }, null, 2))
    process.exit(result.status ?? 1)
  }
  if (result.stdout) process.stdout.write(result.stdout)
}

run('TypeScript', process.execPath, [join(desktop, 'node_modules/typescript/bin/tsc'), '--noEmit'], desktop)
run('Settings metadata', 'python', ['scripts/generate_desktop_settings.py', '--check'])
run('No-comment gate', 'python', ['scripts/check_no_comments.py'])
const unitReport = verifiedOutput('unit-tests.json')
run('Desktop regression tests', process.execPath, [join(desktop, 'node_modules/vitest/vitest.mjs'), 'run', '--silent', '--reporter=json', `--outputFile=${unitReport}`], desktop)
const unit = JSON.parse(readFileSync(unitReport, 'utf8'))
process.stdout.write(`${unit.numPassedTests} desktop tests passed.\n`)
run('Session search and storage', 'python', [
  '-m', 'pytest', 'tests/unit/test_session_search.py', 'tests/unit/test_versioned.py',
  'tests/unit/test_workspace_isolation.py', 'tests/unit/test_session_api_isolation.py',
  '-q', '-p', 'no:cacheprovider', `--basetemp=${verifiedOutput('pytest')}`, '--tb=short',
])
if (!process.argv.includes('--unit-only')) {
  run('Browser acceptance tests', process.execPath, [
    join(desktop, 'node_modules/@playwright/test/cli.js'), 'test',
    'e2e/daily-friction.spec.ts', 'e2e/desktop-startup.spec.ts', '--workers=1', '--reporter=dot',
    `--output=${verifiedOutput('browser')}`,
  ], desktop)
}
const report = verifiedOutput('verification.json')
writeFileSync(report, JSON.stringify({ checks, desktopTests: unit.numPassedTests, browserIncluded: !process.argv.includes('--unit-only') }, null, 2))
process.stdout.write(`Verification report: ${report}\n`)
