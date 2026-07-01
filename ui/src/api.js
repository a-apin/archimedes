/**
 * Shared API fetch helper — safe error handling for the Archimedes frontend.
 *
 * When nginx returns a 502/503 during deploys, res.text() is multi-line HTML
 * (`<html><body>502 Bad Gateway</body></html>`) that would splat raw across
 * the UI if thrown as an Error message. This helper throws a clean, concise
 * error string instead.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/**
 * GET a JSON endpoint. Throws a clean error on non-2xx responses.
 * @param {string} path — API path (e.g. "/api/strategies/")
 * @returns {Promise<any>} parsed JSON
 */
export async function apiGet(path) {
  // credentials:'include' sends the SIWE session cookie so authenticated
  // endpoints work whether or not the API is same-origin.
  const res = await fetch(`${API_BASE}${path}`, { credentials: 'include' })
  if (!res.ok) {
    const err = new Error(`Backend returned ${res.status}`)
    err.status = res.status // so callers can distinguish 404 (not-deployed) from real failures
    throw err
  }
  return res.json()
}

/**
 * POST JSON to an endpoint. Throws a clean error on non-2xx responses.
 * @param {string} path — API path
 * @param {object} body — JSON-serializable body
 * @returns {Promise<any>} parsed JSON
 */
export async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // send SIWE session cookie
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`)
  }
  return res.json()
}

/**
 * DELETE an endpoint. Throws a clean error on non-2xx responses.
 * @param {string} path — API path
 * @returns {Promise<any>} parsed JSON
 */
export async function apiDelete(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`)
  }
  return res.json()
}
