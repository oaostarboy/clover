// Coarse relay admission only. The target gateway must still authenticate the
// original device credential and enforce capabilities and profile restrictions.
export function allowMobileRequest(method, target) {
  if (typeof target !== 'string' || !target.startsWith('/') || target.startsWith('//') || /[\x00-\x20\x7f\\#]/.test(target)) return false;
  const [path, ...rest] = target.split('?');
  if (rest.length > 1 || path.includes('%')) return false;
  const query = new URLSearchParams(rest[0] ?? '');
  if (method === 'POST' && path === '/api/auth/ws-ticket') return !rest.length;
  if (method === 'POST' && (path === '/api/audio/transcribe' || path === '/api/audio/speak')) {
    const entries = [...query];
    return entries.length === 0 || (entries.length === 1 && entries[0][0] === 'profile' && entries[0][1].length > 0);
  }
  if (method !== 'GET') return false;
  // Control view over stills. The gateway decides the grant (control_view); this is only path admission.
  if (path === '/desk/displays') return !rest.length;
  if (path === '/desk/snapshot') return [...query.keys()].every(key => key === 'display') && [...query.values()].every(v => /^[0-9]{1,3}$/.test(v)) && [...query].length <= 1;
  if (path === '/api/sessions') return [...query.keys()].every(key => ['limit', 'offset', 'min_messages', 'archived', 'order'].includes(key));
  return /^\/api\/sessions\/[A-Za-z0-9_-]+\/messages$/.test(path) && !rest.length;
}
