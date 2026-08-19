/**
 * Shared API fetch helper — safe error handling for the Archimedes frontend.
 *
 * When nginx returns a 502/503 during deploys, res.text() is multi-line HTML
 * (`<html><body>502 Bad Gateway</body></html>`) that would splat raw across
 * the UI if thrown as an Error message. This helper throws a clean, concise
 * error string instead.
 */

import { getAddress } from './config'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function walletHeaders() {
  const address = getAddress()
  return address ? { 'X-Wallet-Address': address, 'X-Wallet-Chain-Id': '5042002' } : {}
}

/**
 * GET a JSON endpoint. Throws a clean error on non-2xx responses.
 * @param {string} path — API path (e.g. "/api/strategies/")
 * @returns {Promise<any>} parsed JSON
 */
export async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: walletHeaders(),
  })
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
    headers: { 'Content-Type': 'application/json', ...walletHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = new Error(`Backend returned ${res.status}`)
    err.status = res.status
    throw err
  }
  return res.json()
}

/**
 * POST JSON to an endpoint, returning both the parsed body AND the raw
 * response `Headers` — for callers that need a response header alongside
 * the body (the generation paywall's PAYMENT-RESPONSE settlement receipt,
 * #1296) rather than just the JSON plain apiPost returns. On a non-2xx
 * response the thrown Error also carries `err.detail` — the parsed body's
 * `detail` field (FastAPI's error-body convention) — so callers can read a
 * structured `{reason, message, ...}` instead of only the status code
 * apiPost's Error already carries via `err.status`.
 * @param {string} path — API path
 * @param {object} body — JSON-serializable body
 * @param {object} [extraHeaders] — additional request headers (e.g. Payment-Signature)
 * @returns {Promise<{data: any, headers: Headers}>}
 */
export async function apiPostWithMeta(path, body, extraHeaders = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...walletHeaders(), ...extraHeaders },
    credentials: 'include',
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const err = new Error(`Backend returned ${res.status}`)
    err.status = res.status
    err.detail = data?.detail ?? null
    throw err
  }
  return { data, headers: res.headers }
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
    headers: walletHeaders(),
  })
  if (!res.ok) {
    const err = new Error(`Backend returned ${res.status}`)
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}
