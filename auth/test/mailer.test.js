import assert from 'node:assert/strict'
import test from 'node:test'

import { createMailer } from '../mailer.js'

test('defaults to the console mailer so compose needs no AWS', () => {
  assert.equal(createMailer({}).kind, 'console')
  assert.equal(createMailer({ EMAIL_MAILER: 'console' }).kind, 'console')
})

test('selects SES only when explicitly configured', () => {
  assert.equal(createMailer({ EMAIL_MAILER: 'ses' }).kind, 'ses')
})

test('sender defaults to no-reply@archimedes-arc.com and honors EMAIL_SENDER', () => {
  assert.equal(createMailer({}).sender, 'no-reply@archimedes-arc.com')
  assert.equal(createMailer({ EMAIL_SENDER: 'auth@example.com' }).sender, 'auth@example.com')
})

test('console mailer prints the full message including the URL', async () => {
  const lines = []
  const original = console.log
  console.log = (...args) => lines.push(args.join(' '))
  try {
    await createMailer({}).send({
      to: 'user@example.com',
      subject: 'Verify',
      text: 'https://archimedes-arc.com/verify?token=abc',
    })
  } finally {
    console.log = original
  }
  assert.equal(lines.length, 1)
  assert.ok(lines[0].includes('to=user@example.com'))
  assert.ok(lines[0].includes('https://archimedes-arc.com/verify?token=abc'))
})
