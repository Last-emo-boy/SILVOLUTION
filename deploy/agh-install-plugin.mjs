// Install, trust and enable the silicon-agent plugin in a local AGH instance without a TTY.
//   AGH_HOME=... AGNES_PROFILE=local-dev node deploy/agh-install-plugin.mjs <agnes-harness-repo> [file:./agh-plugin]
// The daemon resolves relative file: sources against the directory it was started from; start it from
// the silicon-agent checkout (`agnes daemon start`) before running this. Reinstalling the same
// integrity is a no-op; a changed plugin is updated through the same preview -> install -> trust -> enable path.
import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const [repo, source = 'file:./agh-plugin'] = process.argv.slice(2)
if (!repo) throw new Error('usage: agh-install-plugin.mjs <agnes-harness-repo> [file:./agh-plugin]')
const sdk = await import(pathToFileURL(resolve(repo, 'packages/sdk/src/index.node.ts')).href)
const home = process.env.AGH_HOME ?? join(process.env.HOME, '.agh')
const profile = process.env.AGNES_PROFILE ?? 'local-dev'
const owner = JSON.parse(await readFile(join(home, 'data/daemon/owner.json'), 'utf8'))
const client = sdk.createClient({ transport: { kind: 'unix', path: owner.socketPath }, auth: { kind: 'local' }, journal: sdk.memoryJournal() })
await client.initialize()
const clientId = await client.clientId()
const stamp = Date.now().toString(36)
const wait = async (operationId) => {
  const deadline = Date.now() + 120_000
  for (;;) {
    const op = await client.packages.operation.get({ profile, operationId })
    if (['completed', 'failed', 'cancelled', 'rolled-back'].includes(op.state)) {
      if (op.state !== 'completed') throw new Error(`package operation ${op.state}: ${JSON.stringify(op.error ?? op).slice(0, 600)}`)
      return op
    }
    if (Date.now() >= deadline) throw new Error('package operation timeout')
    await new Promise((r) => setTimeout(r, 200))
  }
}
try {
  const base = { profile, clientId, source: { type: 'file', ref: source } }
  const preview = (await wait((await client.packages.inspect({ ...base, commandId: `sa-inspect-${stamp}` })).operationId)).preview
  if (!preview?.integrity) throw new Error('no integrity in preview')
  if (preview.blockers?.length) throw new Error(`blockers: ${JSON.stringify(preview.blockers)}`)
  console.log(`preview ${preview.id}@${preview.version} integrity=${preview.integrity} capabilityHash=${preview.capabilityHash}`)
  const installed = (await client.packages.list({ profile })).packages.find((p) => p.id === preview.id)
  if (installed && installed.integrity === preview.integrity && installed.actual === 'running' && installed.trusted) {
    console.log('already installed, trusted and running')
  } else {
    if (installed && installed.integrity !== preview.integrity) {
      await wait((await client.packages.update({ profile, clientId, commandId: `sa-update-${stamp}`, id: preview.id, source: base.source, expectedIntegrity: preview.integrity })).operationId)
      console.log('updated')
    } else if (!installed) {
      await wait((await client.packages.install({ ...base, commandId: `sa-install-${stamp}`, expectedIntegrity: preview.integrity })).operationId)
      console.log('installed')
    }
    await wait((await client.packages.trust({ profile, clientId, commandId: `sa-trust-${stamp}`, id: preview.id, expectedIntegrity: preview.integrity, capabilityHash: preview.capabilityHash })).operationId)
    console.log('trusted')
    await wait((await client.packages.enable({ profile, clientId, commandId: `sa-enable-${stamp}`, id: preview.id })).operationId)
    console.log('enabled')
  }
  const final = (await client.packages.list({ profile })).packages.find((p) => p.id === preview.id)
  console.log(`status: desired=${final?.desired} actual=${final?.actual} trusted=${final?.trusted}${final?.actualReason ? ` reason=${JSON.stringify(final.actualReason)}` : ''}`)
  if (final?.actual !== 'running') process.exitCode = 1
} finally {
  await client.close()
}
