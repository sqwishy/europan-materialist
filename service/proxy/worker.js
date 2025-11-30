// https://developers.cloudflare.com/workers/

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url)

    const forward = new URL(url.pathname + url.search, env.UPSTREAM)

    let headers = new Headers(request.headers)
    headers.set('host', forward.host)
		headers.delete('connection')
		headers.delete('keep-alive')
		headers.delete('upgrade')

    let options = {
      method: request.method,
      headers,
      body: request.body,
    }

    return await fetch(forward, options);
  }
};
