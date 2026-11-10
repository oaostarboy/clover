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
  if (path === '/desk/displays') return !rest.length;
  if (path === '/desk/snapshot') return [...query.entries()].every(([k, v]) => ['display', 'q', 'w'].includes(k) && /^[A-Za-z0-9_.:-]{1,64}$/.test(v)) && new Set(query.keys()).size === [...query.keys()].length;
  // Creations tab: bytes by path and thumbnails by id. The gateway still requires the media right and a file its artifact index listed.
  if (path === '/api/files/download') return [...query.keys()].length === 1 && query.getAll('path').length === 1 && /^\/[^\x00]{1,1024}$/.test(query.get('path'));
  if (path === '/api/artifacts/thumb') return [...query.keys()].every(key => ['id', 'size'].includes(key)) && query.getAll('id').length === 1 && /^[0-9a-f]{16}$/.test(query.get('id')) && query.getAll('size').length <= 1 && /^[0-9]{2,4}$/.test(query.get('size') ?? '480');
  if (path === '/api/sessions') return [...query.keys()].every(key => ['limit', 'offset', 'min_messages', 'archived', 'order'].includes(key));
  return /^\/api\/sessions\/[A-Za-z0-9_-]+\/messages$/.test(path) && !rest.length;
}
