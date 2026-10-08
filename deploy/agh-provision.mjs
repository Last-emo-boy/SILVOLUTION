// Provision a running AGH daemon for silicon-agent: the DeepSeek account (key from DEEPSEEK_API_KEY) and
// the agh-plugin package (install/update -> trust -> enable). Idempotent; run at container start.
//   node deploy/agh-provision.mjs <agh-dist-dir>        (needs the AGH source checkout next to the dist: ../../../..)
// Secrets: the key is submitted to AGH's configuration service once and lands in its credential store.
import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const dist = process.argv[2]
if (!dist) throw new Error('usage: agh-provision.mjs <agh-dist-dir>')
const repo = process.env.AGH_REPO ?? resolve(dist, '../../../..')
const home = process.env.AGH_HOME ?? join(process.env.HOME, '.agh')
const profile = process.env.AGNES_PROFILE ?? 'local-dev'
const pluginSource = process.env.SA_AGH_PLUGIN ?? 'file:./agh-plugin'
const model = process.env.SA_AGH_MODEL ?? process.env.SA_MODEL ?? 'deepseek-flash'
const log = (...a) => console.log('agh-provision:', ...a)

// The dist bundle has no importable SDK: load it from the source checkout (tsx registered by the caller).
const sdk = await import(pathToFileURL(resolve(repo, 'packages/sdk/src/index.node.ts')).href)
const owner = JSON.parse(await readFile(join(home, 'data/daemon/owner.json'), 'utf8'))
const client = sdk.createClient({ transport: { kind: 'unix', path: owner.socketPath }, auth: { kind: 'local' }, journal: sdk.memoryJournal() })
await client.initialize()
try {
  // ---- provider
  const cfg = await client.config.get()
  const have = (cfg.accounts ?? []).find((a) => a.providerId === 'deepseek')
  if (!have || !cfg.configured) {
    const key = process.env.DEEPSEEK_API_KEY
    if (!key) throw new Error('DEEPSEEK_API_KEY is not set and no DeepSeek account is configured')
    const test = await client.config.test({ providerId: 'deepseek', apiKey: key })
    if (!test.models?.some((m) => m.id === model)) throw new Error(`model ${model} not in the DeepSeek catalogue: ${test.models?.map((m) => m.id).join(', ')}`)
    await client.config.save({ providerId: 'deepseek', apiKey: key, model, expectedRevision: cfg.revision ?? 0 })
    log(`configured DeepSeek (${model})`)
  } else log(`DeepSeek account present (${have.model})`)

  // ---- plugin
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
  const base = { profile, clientId, source: { type: 'file', ref: pluginSource } }
  const preview = (await wait((await client.packages.inspect({ ...base, commandId: `sa-inspect-${stamp}` })).operationId)).preview
  if (!preview?.integrity) throw new Error('no integrity in plugin preview')
  if (preview.blockers?.length) throw new Error(`plugin blockers: ${JSON.stringify(preview.blockers)}`)
  const installed = (await client.packages.list({ profile })).packages.find((p) => p.id === preview.id)
  if (installed && installed.integrity === preview.integrity && installed.actual === 'running' && installed.trusted) {
    log(`plugin ${preview.id} up to date and running`)
  } else {
    if (installed && installed.integrity !== preview.integrity) {
      await wait((await client.packages.update({ profile, clientId, commandId: `sa-update-${stamp}`, id: preview.id, source: base.source, expectedIntegrity: preview.integrity })).operationId)
      log('plugin updated')
    } else if (!installed) {
      await wait((await client.packages.install({ ...base, commandId: `sa-install-${stamp}`, expectedIntegrity: preview.integrity })).operationId)
      log('plugin installed')
    }
    await wait((await client.packages.trust({ profile, clientId, commandId: `sa-trust-${stamp}`, id: preview.id, expectedIntegrity: preview.integrity, capabilityHash: preview.capabilityHash })).operationId)
    await wait((await client.packages.enable({ profile, clientId, commandId: `sa-enable-${stamp}`, id: preview.id })).operationId)
    const final = (await client.packages.list({ profile })).packages.find((p) => p.id === preview.id)
    log(`plugin ${preview.id}: desired=${final?.desired} actual=${final?.actual} trusted=${final?.trusted}${final?.actualReason ? ` reason=${JSON.stringify(final.actualReason)}` : ''}`)
    if (final?.actual !== 'running') process.exitCode = 1
  }
} finally {
  await client.close()
}
