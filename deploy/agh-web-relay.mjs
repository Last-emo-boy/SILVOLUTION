// A TCP relay inside the agh container. The harness binds its web launcher and the daemon's browser websocket
// to the loopback only (their origin contract); the job server, another container, reaches them through this
// listener on the container's network address. The client's first line names the loopback target
// ("TARGET 127.0.0.1:4178\n"); the bytes after it are copied both ways untouched -- the job server rewrites
// Host/Origin itself. Targets are confined to the loopback. Only the compose network can reach this port.
import { createConnection, createServer } from 'node:net'

const [listenHost, listenPort] = (process.env.AGH_WEB_RELAY_LISTEN || '0.0.0.0:4177').split(':')

const server = createServer((client) => {
  let head = Buffer.alloc(0)
  const onData = (chunk) => {
    head = Buffer.concat([head, chunk])
    const nl = head.indexOf(10)
    if (nl < 0) { if (head.length > 256) client.destroy(); return }
    client.removeListener('data', onData)
    const line = head.subarray(0, nl).toString('latin1').trim()
    const rest = head.subarray(nl + 1)
    const m = /^TARGET 127\.0\.0\.1:(\d{1,5})$/.exec(line)
    if (!m) { client.destroy(); return }
    const up = createConnection({ host: '127.0.0.1', port: Number(m[1]) })
    const drop = () => { client.destroy(); up.destroy() }
    up.on('error', drop); client.on('error', drop)
    up.on('close', () => client.end()); client.on('close', () => up.end())
    up.on('connect', () => { if (rest.length) up.write(rest); client.pipe(up); up.pipe(client) })
  }
  client.on('data', onData)
})
server.on('error', (e) => { console.error(`agh web relay: ${e.message}`); process.exit(1) })
server.listen(Number(listenPort), listenHost, () => console.log(`agh web relay ${listenHost}:${listenPort} -> loopback targets`))
