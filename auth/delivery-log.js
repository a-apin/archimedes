// Per-address record of what actually happened to each outbound message.
//
// WHY THIS EXISTS (#1748 item 2). Before this file, every send in this service
// was fire-and-forget with a `.catch` that logged an error NAME to stdout and
// nothing else. That is enough for an operator grepping CloudWatch and useless
// to the person waiting for the mail: POST /api/auth/send-verification-email
// answered `200 {status:true}` forever — for an address SES had already
// dropped onto the account suppression list, for an address whose last send
// threw, for every address. The account owner had no way to tell "in your spam
// folder" from "SES is silently binning this".
//
// So each send writes one row here: the SES MessageId when SES accepted it,
// the error NAME when it did not. `GET /api/auth/verification-status`
// (server.js) reads those rows back for the signed-in caller's own address.
//
// WHAT IS DELIBERATELY NOT STORED: the message body, the subject, the
// verification URL (a one-time bearer sign-in credential — see auth.js's
// `emailVerification.expiresIn` comment), and the error MESSAGE. Only the
// error's constructor name, which is a fixed AWS SDK vocabulary
// (`MessageRejected`, `AccessDeniedException`, ...) and cannot carry an
// address, a token, or a body fragment into the log.
//
// The table is `auth_email_deliveries`, owned by the PYTHON side's alembic
// chain (backend/migrations/versions/d4b1f7c8e206_auth_email_deliveries.py)
// with a matching ORM model in backend/archimedes/models/account.py — the same
// split every other `auth_*` table already lives under: Better Auth (and now
// this file) writes the rows, alembic owns the DDL. auth/ has no migration
// runner of its own and does not grow one for this.

import { randomUUID } from 'node:crypto'

export const DELIVERY_TABLE = 'auth_email_deliveries'

/** Kinds recorded, one per mail this service can send. */
export const DELIVERY_KINDS = Object.freeze({
  VERIFICATION: 'verification',
  RESET: 'reset',
  CHANGE_EMAIL: 'change_email',
  ACCOUNT_CHANGE: 'account_change',
})

/** How far back `recent()` looks. Bounds the query; nothing older is actionable. */
export const RECENT_WINDOW_SECONDS = 24 * 60 * 60

/**
 * Addresses are compared case-insensitively here and only here.
 *
 * The session's `user.email` and the address a send went to are the same
 * string today, but the change-email flow (auth.js `user.changeEmail`) can
 * move an account's address while old rows keep the old one — so the read
 * side matches on a normalized address, not on user id alone.
 */
export function normalizeAddress(email) {
  return String(email ?? '').trim().toLowerCase()
}

/** Last ordering key this process handed out. Never goes backwards. */
let lastIssuedAt = 0

/**
 * The row's ordering key — and why it is not simply `new Date()`.
 *
 * `recent()` promises most-recent-first, and verification-status.js reads
 * `rows[0]` as THE latest attempt. That one row decides whether the account
 * owner is told "our provider accepted it" or "the last attempt was refused",
 * which is the entire claim this feature makes.
 *
 * `new Date()` has millisecond resolution, and two records written back to
 * back — a send and the record of the failure it threw, a signup send followed
 * immediately by a resend — land on the SAME millisecond as a matter of
 * routine (measured on this code: 197 pairs in 200). `ORDER BY created_at
 * DESC` is then a tie, the database may return either row first, and a
 * `failed` send can be reported as `sent`: precisely the optimistic claim this
 * file exists to stop. The ordering was never guaranteed; it was lucky, which
 * is how auth/test/delivery-feedback.test.js could pass on one machine and
 * fail on a faster CI runner.
 *
 * So the key issued here is STRICTLY increasing within a process: never behind
 * the wall clock, never equal to the row before it. The drift that introduces
 * is bounded by how many rows one process writes inside a single millisecond
 * (a handful, at a few mails per second) and buys an ordering claim that is
 * true rather than lucky.
 *
 * RESIDUAL, stated rather than hidden: this is per PROCESS. If the auth
 * service ever runs more than one task (`ecs_service_desired_count` is 1 today
 * but the service autoscales), two rows for one address written by two tasks
 * inside the same millisecond can still tie, and no key available here can
 * recover an order those two events do not have.
 */
export function nextCreatedAt(now = Date.now()) {
  lastIssuedAt = Math.max(now, lastIssuedAt + 1)
  return new Date(lastIssuedAt)
}

/**
 * The "no delivery log configured" implementation.
 *
 * `recent()` returns `null`, NOT `[]`. The difference is the whole point:
 * `[]` means "we looked and nothing was ever sent", `null` means "we cannot
 * see". verification-status.js renders those as two different states
 * (`unknown` vs `sent`/`unknown`) rather than letting an unconfigured log
 * masquerade as an empty history — the fail-soft-into-a-plausible-substitute
 * failure CLAUDE.md § "Fail-soft is correct for optional configuration and
 * wrong for anything a claim depends on" is about.
 */
export function nullDeliveryLog() {
  return {
    kind: 'none',
    async record() { return null },
    async recent() { return null },
  }
}

/**
 * @param {{query: (text: string, params?: unknown[]) => Promise<{rows: object[]}>}|null} db
 *   Anything with node-postgres' `query(text, params)` shape. `createAuth`'s
 *   Pool satisfies it; auth/test/ passes a small fake. A missing or
 *   wrong-shaped db degrades to `nullDeliveryLog()` — local `docker compose`
 *   runs the console mailer against a database that has been migrated, so
 *   this is a real path, not a defensive branch.
 */
export function createDeliveryLog(db) {
  if (!db || typeof db.query !== 'function') return nullDeliveryLog()

  return {
    kind: 'sql',

    /**
     * Never throws. A delivery RECORD failing must not turn into a failed
     * send: the mail is the product, the row is the receipt. Callers get
     * `null` and a greppable marker goes to the log.
     */
    async record({ userId = null, email, kind, status, messageId = null, error = null }) {
      const address = normalizeAddress(email)
      if (!address || !kind || !status) return null
      const row = {
        id: randomUUID().replace(/-/g, ''),
        userId: userId || null,
        email: address,
        kind,
        status,
        messageId: messageId || null,
        error: error || null,
        // Not `new Date()` — see nextCreatedAt above. `recent()`'s
        // most-recent-first contract is only as good as this key is total.
        createdAt: nextCreatedAt(),
      }
      try {
        await db.query(
          `INSERT INTO ${DELIVERY_TABLE}`
          + ' (id, user_id, email, kind, status, message_id, error, created_at)'
          + ' VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
          [row.id, row.userId, row.email, row.kind, row.status, row.messageId, row.error, row.createdAt],
        )
        return row
      } catch (cause) {
        // The FK onto auth_users is ON DELETE CASCADE, so a send whose account
        // was deleted between dispatch and record lands here — expected, and
        // still worth one line, because the same marker covers "the table is
        // missing because the migration has not run", which is not.
        console.error('EMAIL_DELIVERY_RECORD_FAILED', {
          kind, status, error: cause instanceof Error ? cause.name : 'UnknownError',
        })
        return null
      }
    },

    /**
     * Most-recent-first rows for one address+kind inside RECENT_WINDOW_SECONDS.
     * Returns `null` — never `[]` — when the query itself fails, for the
     * reason nullDeliveryLog() spells out above.
     */
    async recent({ email, kind, limit = 20, now = Date.now() }) {
      const address = normalizeAddress(email)
      if (!address || !kind) return null
      const since = new Date(now - RECENT_WINDOW_SECONDS * 1000)
      try {
        const result = await db.query(
          'SELECT status, message_id, error, created_at'
          + ` FROM ${DELIVERY_TABLE}`
          + ' WHERE email = $1 AND kind = $2 AND created_at >= $3'
          + ' ORDER BY created_at DESC LIMIT $4',
          [address, kind, since, limit],
        )
        return (result?.rows ?? []).map(row => ({
          status: row.status,
          messageId: row.message_id ?? null,
          error: row.error ?? null,
          createdAt: new Date(row.created_at),
        }))
      } catch (cause) {
        console.error('EMAIL_DELIVERY_READ_FAILED', {
          kind, error: cause instanceof Error ? cause.name : 'UnknownError',
        })
        return null
      }
    },
  }
}
