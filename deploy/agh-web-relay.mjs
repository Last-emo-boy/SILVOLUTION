// A TCP relay inside the agh container: the harness's web launcher binds 127.0.0.1:4177 (its origin contract);
// the job server, another container, reaches it through this listener on the container's network address.
// Pure byte copy, no HTTP parsing: the job server rewrites Host/Origin itself. Only the compose network can
// reach this port (nothing is published on the host).
import { createConnection, createServer } from 'node:net'

const [listenHost, listenPort] = (process.env.AGH_WEB_RELAY_LISTEN || '0.0.0.0:4177').split(':')
const [targetHost, targetPort] = (process.env.AGH_WEB_RELAY_TARGET || '127.0.0.1:4177').split(':')

const server = createServer((client) => {
  const up = createConnection({ host: targetHost, port: Number(targetPort) })
  const drop = () => { client.destroy(); up.destroy() }
  up.on('error', drop); client.on('error', drop)
  up.on('close', () => client.end()); client.on('close', () => up.end())
  client.pipe(up); up.pipe(client)
})
server.on('error', (e) => { console.error(`agh web relay: ${e.message}`); process.exit(1) })
server.listen(Number(listenPort), listenHost, () => console.log(`agh web relay ${listenHost}:${listenPort} -> ${targetHost}:${targetPort}`))
