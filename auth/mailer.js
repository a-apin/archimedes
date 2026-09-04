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
  // #1804. A send that does NOT name a configuration set produces no bounce
  // event, no complaint event, nothing — SES's per-message feedback is
  // published by the configuration set, not by the identity, so an unset
  // ConfigurationSetName is exactly the deaf state this repo was in.
  //
  // Blank/unset is still honoured rather than defaulted to a literal, and the
  // property is omitted (not sent as an empty string, which SES rejects):
  // `infra/ses_events.tf` is what creates the set and `infra/ecs.tf` is what
  // sets this variable, both in the same terraform apply, so before that apply
  // the only truthful thing to do is send the way we always have. The name
  // itself is never hardcoded here for the same reason — the terraform
  // resource is its single source, and backend/tests/test_ses_event_wiring.py
  // fails if the two files stop agreeing.
  const configurationSet = (env.SES_CONFIGURATION_SET || '').trim()

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
      configurationSet,
      async send({ to, subject, text }) {
        const { ses, client } = await sesClient()
        await client.send(new ses.SendEmailCommand({
          FromEmailAddress: sender,
          Destination: { ToAddresses: [to] },
          Content: { Simple: { Subject: { Data: subject }, Body: { Text: { Data: text } } } },
          ...(configurationSet ? { ConfigurationSetName: configurationSet } : {}),
        }))
      },
    }
  }

  return {
    kind: 'console',
    sender,
    configurationSet,
    async send({ to, subject, text }) {
      console.log(`[mailer:console] from=${sender} to=${to} subject=${subject}\n${text}`)
    },
  }
}
