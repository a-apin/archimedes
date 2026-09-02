// Delivery feedback for verification mail (#1748 item 2).
//
// The observation this file is the guard for: POST /api/auth/send-verification-email
// answered `200 {status:true}` forever — for an address SES had already dropped
// onto the account suppression list, for an address whose last send threw, for
// every address. Nothing in the product could tell those apart, so nothing
// could tell the user.
//
// Four units, each tested against a stub at its own boundary and nothing
// further out:
//   delivery-log.js       — a fake `{query}` (the node-postgres shape)
//   suppression.js        — a fake SESv2 client via the `loadClient` seam
//   verification-status.js — pure; no stub needed beyond the two above
//   server.js             — a fake `auth.api.getSession`, over a real socket
//
// Hermetic: no AWS, no Postgres, no network. The AWS SDK is never even
// imported — `loadClient` short-circuits the lazy import in both modules.

import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import {
  createDeliveryLog,
  DELIVERY_KINDS,
  nextCreatedAt,
  nullDeliveryLog,
  normalizeAddress,
} from '../delivery-log.js'
import { createMailer } from '../mailer.js'
import { createRequestHandler } from '../server.js'
import { createSuppressionLookup } from '../suppression.js'
import {
  RESEND_WINDOW_MAX,
  RESEND_WINDOW_SECONDS,
  SPAM_HINT_AFTER_SENDS,
  resolveVerificationStatus,
  VERIFICATION_STATES,
} from '../verification-status.js'

// ── Fakes ────────────────────────────────────────────────────────────────

/** Minimal node-postgres shape over an in-memory array. */
function fakeDb({ failOn = null } = {}) {
  const rows = []
  return {
    rows,
    async query(text, params) {
      if (failOn && text.includes(failOn)) throw Object.assign(new Error('boom'), { name: 'DatabaseError' })
      if (text.startsWith('INSERT')) {
        const [id, user_id, email, kind, status, message_id, error, created_at] = params
        rows.push({ id, user_id, email, kind, status, message_id, error, created_at, seq: rows.length })
        return { rows: [] }
      }
      const [email, kind, since, limit] = params
      const matched = rows
        .filter(row => row.email === email && row.kind === kind && row.created_at >= since)
        // Postgres gives NO order to rows that tie on `created_at`, so this
        // picks the worst legal one — the older row first, the exact inverse
        // of the contract — rather than letting a stable sort quietly return
        // insertion order and make a tie look like a correct answer. A tie
        // reaching here means "most-recent-first" is a coin flip in
        // production, which is what delivery-log.js's monotonic ordering key
        // exists to prevent. (Before that key, this test passed locally and
        // failed on CI on exactly this tie.)
        .sort((a, b) => (b.created_at - a.created_at) || (a.seq - b.seq))
        .slice(0, limit)
      return { rows: matched }
    },
  }
}

/** A SESv2 stub shaped like the two commands these modules actually issue. */
function fakeSes({ suppressed = null, throws = null, sendResult = { MessageId: 'ses-message-1' } } = {}) {
  const calls = []
  const ses = {
    GetSuppressedDestinationCommand: class { constructor(input) { this.kind = 'get-suppressed'; this.input = input } },
    SendEmailCommand: class { constructor(input) { this.kind = 'send'; this.input = input } },
  }
  const client = {
    async send(command) {
      calls.push(command)
      if (command.kind === 'send') {
        if (throws) throw Object.assign(new Error('rejected'), { name: throws })
        return sendResult
      }
      if (throws) throw Object.assign(new Error('lookup'), { name: throws })
      if (!suppressed) throw Object.assign(new Error('not found'), { name: 'NotFoundException' })
      return { SuppressedDestination: suppressed }
    },
  }
  return { calls, loadClient: async () => ({ ses, client }) }
}

async function listen(handler) {
  const server = createServer(handler)
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return {
    baseURL: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise(resolve => server.close(resolve)),
  }
}

const NOW = Date.parse('2026-09-01T22:00:00.000Z')
const ago = seconds => new Date(NOW - seconds * 1000)

/** A history fixture straight from delivery-log.recent()'s return shape. */
function history(...entries) {
  return {
    async recent() { return entries },
  }
}
const unreadableLog = { async recent() { return null } }
const cleanSuppression = { async check() { return { checked: true, suppressed: false, reason: null, since: null } } }

// ── delivery-log.js ──────────────────────────────────────────────────────

test('a send is recorded per address with its SES MessageId, and read back most-recent-first', async () => {
  const db = fakeDb()
  const log = createDeliveryLog(db)

  await log.record({
    userId: 'u1', email: 'Dan@Example.com', kind: DELIVERY_KINDS.VERIFICATION,
    status: 'sent', messageId: 'ses-1',
  })
  await log.record({
    userId: 'u1', email: 'dan@example.com', kind: DELIVERY_KINDS.VERIFICATION,
    status: 'failed', error: 'MessageRejected',
  })

  // Addresses are normalized on the way in, so the mixed-case send is found.
  assert.equal(normalizeAddress('  Dan@Example.COM '), 'dan@example.com')
  const rows = await log.recent({ email: 'DAN@example.com', kind: DELIVERY_KINDS.VERIFICATION, now: Date.now() })
  assert.equal(rows.length, 2)
  assert.equal(rows[0].status, 'failed')
  assert.equal(rows[0].error, 'MessageRejected')
  assert.equal(rows[1].status, 'sent')
  assert.equal(rows[1].messageId, 'ses-1')

  // Other kinds share the table and must not bleed into the verification view.
  await log.record({ userId: 'u1', email: 'dan@example.com', kind: DELIVERY_KINDS.RESET, status: 'sent' })
  const still = await log.recent({ email: 'dan@example.com', kind: DELIVERY_KINDS.VERIFICATION, now: Date.now() })
  assert.equal(still.length, 2)
})

test('two records in the same millisecond still come back in order — the key is monotonic, not the clock', async () => {
  // The bug this pins: `createdAt` used to be `new Date()`, so back-to-back
  // records shared a millisecond, `ORDER BY created_at DESC` was a tie, and
  // `rows[0]` — the row verification-status.js reads as THE latest attempt —
  // was whichever one the sort happened to leave in front. A `failed` send
  // reported as `sent` is the optimistic claim this whole feature exists to
  // end. It passed on a Mac and failed on a CI runner for no reason but speed.
  const frozen = Date.now()
  const realNow = Date.now
  Date.now = () => frozen // the wall clock does not advance at all
  const db = fakeDb()
  const log = createDeliveryLog(db)
  try {
    await log.record({ email: 'tie@example.com', kind: DELIVERY_KINDS.VERIFICATION, status: 'sent', messageId: 'ses-1' })
    await log.record({ email: 'tie@example.com', kind: DELIVERY_KINDS.VERIFICATION, status: 'failed', error: 'MessageRejected' })
  } finally {
    Date.now = realNow
  }

  assert.equal(db.rows.length, 2)
  assert.ok(
    db.rows[1].created_at.getTime() > db.rows[0].created_at.getTime(),
    'a stopped clock must still produce a strictly increasing ordering key',
  )
  const rows = await log.recent({ email: 'tie@example.com', kind: DELIVERY_KINDS.VERIFICATION, now: frozen + 1000 })
  assert.equal(rows[0].status, 'failed', 'the newest attempt is the one the status endpoint reports on')

  // The key itself, directly: same input millisecond, three distinct answers.
  const stamps = [nextCreatedAt(frozen), nextCreatedAt(frozen), nextCreatedAt(frozen)].map(d => d.getTime())
  assert.deepEqual([...new Set(stamps)].length, 3)
  assert.ok(stamps[0] < stamps[1] && stamps[1] < stamps[2])
  // Never BEHIND the wall clock either — a stamp that lags would age rows out
  // of the 24h window early and understate the resend count.
  assert.ok(nextCreatedAt(frozen + 10_000).getTime() >= frozen + 10_000)
})

test('an unreadable log answers null, never an empty history — they are different claims', async () => {
  assert.equal(await nullDeliveryLog().recent({ email: 'a@b.c', kind: DELIVERY_KINDS.VERIFICATION }), null)
  assert.equal(createDeliveryLog(null).kind, 'none')

  const failing = createDeliveryLog(fakeDb({ failOn: 'SELECT' }))
  assert.equal(await failing.recent({ email: 'a@b.c', kind: DELIVERY_KINDS.VERIFICATION }), null)
})

test('a failed record never turns into a failed send', async () => {
  const log = createDeliveryLog(fakeDb({ failOn: 'INSERT' }))
  // Resolves (with null), does not reject: the mail is the product, the row
  // is the receipt.
  assert.equal(
    await log.record({ email: 'a@b.c', kind: DELIVERY_KINDS.VERIFICATION, status: 'sent' }),
    null,
  )
})

// ── mailer.js ────────────────────────────────────────────────────────────

test('the SES mailer records the MessageId on success and the error NAME on failure', async () => {
  const db = fakeDb()
  const log = createDeliveryLog(db)

  const ok = fakeSes({ sendResult: { MessageId: 'ses-abc' } })
  const mailer = createMailer({ EMAIL_MAILER: 'ses' }, { deliveryLog: log, loadClient: ok.loadClient })
  assert.equal(mailer.kind, 'ses')
  await mailer.send({
    to: 'dan@example.com', subject: 'Verify your Archimedes account', text: 'https://x/verify?token=t',
    kind: DELIVERY_KINDS.VERIFICATION, userId: 'u1',
  })
  assert.equal(db.rows.length, 1)
  assert.equal(db.rows[0].status, 'sent')
  assert.equal(db.rows[0].message_id, 'ses-abc')

  const bad = fakeSes({ throws: 'MessageRejected' })
  const failing = createMailer({ EMAIL_MAILER: 'ses' }, { deliveryLog: log, loadClient: bad.loadClient })
  await assert.rejects(
    () => failing.send({ to: 'dan@example.com', subject: 's', text: 't', kind: DELIVERY_KINDS.VERIFICATION }),
    /rejected/,
    'the send must still reject — auth.js\'s own .catch is what makes it fail-soft',
  )
  assert.equal(db.rows.length, 2)
  assert.equal(db.rows[1].status, 'failed')
  assert.equal(db.rows[1].error, 'MessageRejected')

  // Never the error MESSAGE, never the body, never the verification URL.
  for (const row of db.rows) {
    assert.equal(Object.hasOwn(row, 'text'), false)
    assert.doesNotMatch(JSON.stringify(row), /token=|https?:\/\//)
  }
})

// ── suppression.js ───────────────────────────────────────────────────────

test('the suppression lookup reports BOUNCE, absence, and its own failure as three different things', async () => {
  const bounced = fakeSes({
    suppressed: { EmailAddress: 'gone@example.com', Reason: 'BOUNCE', LastUpdateTime: new Date('2026-08-30T10:00:00Z') },
  })
  const hit = await createSuppressionLookup({ EMAIL_MAILER: 'ses' }, { loadClient: bounced.loadClient })
    .check('gone@example.com')
  assert.deepEqual(
    { checked: hit.checked, suppressed: hit.suppressed, reason: hit.reason },
    { checked: true, suppressed: true, reason: 'BOUNCE' },
  )
  assert.equal(hit.since, '2026-08-30T10:00:00.000Z')

  const clean = fakeSes()
  const miss = await createSuppressionLookup({ EMAIL_MAILER: 'ses' }, { loadClient: clean.loadClient })
    .check('fine@example.com')
  assert.deepEqual({ checked: miss.checked, suppressed: miss.suppressed }, { checked: true, suppressed: false })

  // AccessDenied is NOT "not suppressed" — it is "we could not look".
  const denied = fakeSes({ throws: 'AccessDeniedException' })
  const unknown = await createSuppressionLookup({ EMAIL_MAILER: 'ses' }, { loadClient: denied.loadClient })
    .check('who@example.com')
  assert.deepEqual({ checked: unknown.checked, suppressed: unknown.suppressed }, { checked: false, suppressed: false })
  assert.equal(unknown.error, 'AccessDeniedException')
})

test('the console mailer has no suppression state to report, and says so rather than "clean"', async () => {
  const lookup = createSuppressionLookup({})
  assert.equal(lookup.kind, 'disabled')
  const result = await lookup.check('anyone@example.com')
  assert.equal(result.checked, false)
  assert.equal(result.suppressed, false)
  assert.match(result.detail, /SES/)
})

// ── verification-status.js: one state per fact ───────────────────────────

test('SUPPRESSED wins over everything — resending cannot work, so nothing else is worth saying', async () => {
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'gone@example.com', emailVerified: false },
    deliveryLog: history({ status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(5) }),
    suppression: { async check() { return { checked: true, suppressed: true, reason: 'BOUNCE', since: '2026-08-30T10:00:00.000Z' } } },
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.SUPPRESSED)
  assert.equal(status.suppression.reason, 'BOUNCE')
  // "Check your spam folder" is wrong advice for an address SES is binning.
  assert.equal(status.checkSpam, false)
})

test('a suppression lookup that FAILED never reads as suppressed and never reads as clean', async () => {
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'who@example.com', emailVerified: false },
    deliveryLog: history({ status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(5) }),
    suppression: { async check() { return { checked: false, suppressed: false, reason: null, since: null, error: 'ThrottlingException' } } },
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.SENT)
  assert.equal(status.suppression.checked, false)
  assert.equal(status.suppression.suppressed, false)
})

test('FAILED surfaces the last send\'s error name instead of a green "sent"', async () => {
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history(
      { status: 'failed', messageId: null, error: 'MessageRejected', createdAt: ago(3) },
      { status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(400) },
    ),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.FAILED)
  assert.equal(status.lastError, 'MessageRejected')
})

test('RATE_LIMITED once the window is full, with a wait computed from the sends that fill it', async () => {
  const entries = []
  for (let i = 0; i < RESEND_WINDOW_MAX; i += 1) {
    entries.push({ status: 'sent', messageId: `ses-${i}`, error: null, createdAt: ago(10 + i * 5) })
  }
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history(...entries),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.RATE_LIMITED)
  assert.equal(status.sendsInWindow, RESEND_WINDOW_MAX)
  // The blocking send is the RESEND_WINDOW_MAX-th newest — the one whose
  // ageing-out drops the in-window count back under the cap.
  const blockingAgeSeconds = 10 + (RESEND_WINDOW_MAX - 1) * 5
  assert.equal(status.retryAfterSeconds, RESEND_WINDOW_SECONDS - blockingAgeSeconds)
  assert.equal(status.resendWindowMax, RESEND_WINDOW_MAX)
})

test('SENT, and the spam hint only appears after more than one send', async () => {
  const one = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history({ status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(300) }),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(one.state, VERIFICATION_STATES.SENT)
  assert.equal(one.sends, 1)
  assert.equal(one.checkSpam, false)
  assert.equal(one.retryAfterSeconds, 0, 'one send an hour ago does not block the next')

  const entries = []
  for (let i = 0; i < SPAM_HINT_AFTER_SENDS; i += 1) {
    entries.push({ status: 'sent', messageId: `ses-${i}`, error: null, createdAt: ago(300 + i * 100) })
  }
  const many = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history(...entries),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(many.state, VERIFICATION_STATES.SENT)
  assert.equal(many.checkSpam, true)
})

test('the spam hint counts ACCEPTED sends; the rate window counts every attempt', async () => {
  // A send that threw did not go anywhere, so it must not inflate "N have
  // gone out" — but it DID consume a rate-limit slot on its way in, so it
  // must still count toward the window. Two different questions, two counts.
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history(
      { status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(5) },
      { status: 'failed', messageId: null, error: 'MessageRejected', createdAt: ago(10) },
      { status: 'sent', messageId: 'ses-2', error: null, createdAt: ago(15) },
    ),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(status.sends, 2, 'a failed send is not a send')
  assert.equal(status.sendsInWindow, 3, 'a failed send still consumed a rate-limit slot')
  assert.equal(status.checkSpam, true)
})

test('an unreadable delivery log is UNKNOWN, not "sent" — the state that used to be a silent 200', async () => {
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: unreadableLog,
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.UNKNOWN)
  assert.equal(status.sends, null, 'null means "we cannot see", 0 would claim "nothing was ever sent"')

  const never = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: false },
    deliveryLog: history(),
    suppression: cleanSuppression,
    now: NOW,
  })
  assert.equal(never.state, VERIFICATION_STATES.UNKNOWN)
  assert.equal(never.sends, 0)
})

test('a verified account short-circuits and spends no SES call proving it', async () => {
  let checks = 0
  const status = await resolveVerificationStatus({
    user: { id: 'u1', email: 'dan@example.com', emailVerified: true },
    deliveryLog: history({ status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(5) }),
    suppression: { async check() { checks += 1; return { checked: true, suppressed: true, reason: 'BOUNCE' } } },
    now: NOW,
  })
  assert.equal(status.state, VERIFICATION_STATES.VERIFIED)
  assert.equal(checks, 0)
})

// ── server.js: the endpoint ──────────────────────────────────────────────

function statusHandler({ session, verificationStatus }) {
  return createRequestHandler({
    auth: { api: { getSession: async () => session }, handler: async () => new Response(null, { status: 404 }) },
    nodeHandler: (_req, res) => { res.statusCode = 404; res.end() },
    providers: [],
    verificationStatus,
  })
}

test('GET /api/auth/verification-status is session-required and reports the SESSION\'s address only', async t => {
  const seen = []
  const app = await listen(statusHandler({
    session: { user: { id: 'u1', email: 'owner@example.com', emailVerified: false } },
    verificationStatus: async user => {
      seen.push(user.email)
      return resolveVerificationStatus({
        user,
        deliveryLog: history({ status: 'sent', messageId: 'ses-1', error: null, createdAt: ago(300) }),
        suppression: cleanSuppression,
        now: NOW,
      })
    },
  }))
  t.after(app.close)

  // A query string naming SOMEONE ELSE's address changes nothing: the handler
  // never reads one. This is what keeps the endpoint from becoming the
  // per-address oracle Better Auth's constant-time floor on the POST prevents.
  const response = await fetch(`${app.baseURL}/api/auth/verification-status?email=victim@example.com`)
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('cache-control'), 'no-store')
  const body = await response.json()
  assert.equal(body.state, VERIFICATION_STATES.SENT)
  assert.equal(body.email, 'owner@example.com')
  assert.deepEqual(seen, ['owner@example.com'])
})

test('no session is 401, and an unwired status route is 503 — never a cheerful default', async t => {
  const anonymous = await listen(statusHandler({ session: null, verificationStatus: async () => ({ state: 'sent' }) }))
  t.after(anonymous.close)
  assert.equal((await fetch(`${anonymous.baseURL}/api/auth/verification-status`)).status, 401)

  const unwired = await listen(statusHandler({
    session: { user: { id: 'u1', email: 'owner@example.com', emailVerified: false } },
    verificationStatus: null,
  }))
  t.after(unwired.close)
  const response = await fetch(`${unwired.baseURL}/api/auth/verification-status`)
  assert.equal(response.status, 503)
  assert.doesNotMatch(JSON.stringify(await response.json()), /sent/)
})

test('the status route is matched BEFORE the /api/auth/ catch-all, which knows nothing about it', async t => {
  let handedToBetterAuth = 0
  const app = await listen(createRequestHandler({
    auth: { api: { getSession: async () => ({ user: { id: 'u1', email: 'o@example.com', emailVerified: true } }) } },
    nodeHandler: (_req, res) => { handedToBetterAuth += 1; res.statusCode = 404; res.end() },
    providers: [],
    verificationStatus: async user => resolveVerificationStatus({
      user, deliveryLog: history(), suppression: cleanSuppression, now: NOW,
    }),
  }))
  t.after(app.close)

  assert.equal((await fetch(`${app.baseURL}/api/auth/verification-status`)).status, 200)
  assert.equal(handedToBetterAuth, 0)
  // A path Better Auth DOES own still reaches it.
  assert.equal((await fetch(`${app.baseURL}/api/auth/get-session`)).status, 404)
  assert.equal(handedToBetterAuth, 1)
})
