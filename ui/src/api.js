const j = async (r) => { if (!r.ok) throw new Error(await r.text()); return r.json() }
export const api = {
  health: () => fetch('/api/health').then(j),
  connections: () => fetch('/api/connections').then(j),
  connectServer: (b) => fetch('/api/connect/server', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then(j),
  connectMuse: (b) => fetch('/api/connect/muse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then(j),
  reports: () => fetch('/api/reports').then(j),
  report: (id) => fetch(`/api/reports/${id}`).then(j),
  metrics: () => fetch('/api/metrics?limit=500').then(j),
  logs: (q) => fetch(`/api/logs/search?q=${encodeURIComponent(q)}`).then(j),
  run: () => fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(j),
  apply: (id) => fetch(`/api/fixes/${id}/apply`, { method: 'POST' }).then(j),
  rerun: (id) => fetch(`/api/fixes/${id}/rerun`, { method: 'POST' }).then(j),
}
