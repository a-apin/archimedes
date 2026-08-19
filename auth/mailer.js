// Email delivery for Better Auth verification mail.
//
// Two modes, selected explicitly via EMAIL_MAILER:
//   "ses"     — Amazon SESv2, credentials/region from the task role and
//               AWS_REGION (nothing stored here). Deployed environments.
//   "console" — logs the message to stdout. Local dev/compose: composing the
//               stack needs no AWS account, and the verification URL is
//               readable straight from `docker compose logs auth`.
//
// The default is "console" so a deploy that forgets the env var degrades to a
// loudly-visible log line rather than a silent SES failure. The SES SDK is
// imported lazily inside the ses branch only, so console mode (and its tests)
// never load AWS code at all.

export function createMailer(env = process.env) {
  const sender = env.EMAIL_SENDER || 'no-reply@archimedes-arc.com'

  if ((env.EMAIL_MAILER || 'console') === 'ses') {
    let clientPromise = null
    function sesClient() {
      clientPromise ??= import('@aws-sdk/client-sesv2').then(
        ses => ({ ses, client: new ses.SESv2Client({}) }),
      )
      return clientPromise
    }
    return {
      kind: 'ses',
      sender,
      async send({ to, subject, text }) {
        const { ses, client } = await sesClient()
        await client.send(new ses.SendEmailCommand({
          FromEmailAddress: sender,
          Destination: { ToAddresses: [to] },
          Content: { Simple: { Subject: { Data: subject }, Body: { Text: { Data: text } } } },
        }))
      },
    }
  }

  return {
    kind: 'console',
    sender,
    async send({ to, subject, text }) {
      console.log(`[mailer:console] from=${sender} to=${to} subject=${subject}\n${text}`)
    },
  }
}
