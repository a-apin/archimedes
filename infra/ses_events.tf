# ── SES event feedback — the PUSH half of the bounce signal (#1804) ─────────
#
# THE DEFECT THIS CLOSES. Nothing in this account subscribed to SES delivery
# feedback: no `aws_sesv2_configuration_set`, no event destination, no topic
# (`ses_inbound.tf` next door is INBOUND mail for privacy@, a different thing
# entirely). A hard bounce therefore reached us only as an entry AWS silently
# added to the account suppression list — visible to an operator running
# `archimedes.scripts.ses_suppression list` and to nobody else, ever. The
# product's own view of that account was "emailVerified = false", which is the
# same value it holds for someone who simply has not clicked the link yet, so
# a fake or dead address is indistinguishable from an impatient human and is
# locked out of the free tier (`services/free_generations.py`) with no
# explanation and no path forward.
#
# THE LOOP THIS BUILDS.
#
#   SES send (auth container, ConfigurationSetName = the set below)
#        │
#        ├─ BOUNCE / COMPLAINT / REJECT / DELIVERY event
#        ▼
#   aws_sesv2_configuration_set_event_destination.feedback
#        ▼
#   aws_sns_topic.ses_events            (fan-out point; SES publishes here)
#        ▼
#   aws_sqs_queue.ses_events            (DURABLE — 14-day retention; the queue
#        │                               is what makes the consumer allowed to
#        │                               be periodic rather than always-on)
#        ▼
#   python -m archimedes.scripts.ses_events drain
#        ▼
#   auth_users.emailBouncedAt / emailBounceKind
#        ▼
#   signup + self-service resend refuse the address, with a typed reason
#        (auth/auth.js hooks.before)
#
# WHY SNS→SQS AND NOT SES→SQS DIRECTLY. SESv2 event destinations cannot target
# SQS; the supported destinations are SNS, EventBridge, Kinesis Firehose,
# CloudWatch and Pinpoint. SNS alone is not durable enough to be the whole
# answer — an SNS topic with no healthy subscriber DROPS the notification, so
# a consumer that is down (or, as here, one that runs periodically rather than
# continuously) would lose every bounce that arrived while it was not
# listening. The queue is the buffer that makes those losses impossible: a
# bounce sits in it until something deletes it, and a message that fails five
# receives lands in the DLQ instead of spinning forever.
#
# WHAT IS DELIBERATELY NOT HERE. No EventBridge schedule and no new ECS task
# definition/service for the consumer — `archimedes.scripts.ses_events` is a
# management command in the same directory (and with the same operator-run
# shape) as `ses_suppression.py`, and standing up a new scheduled compute unit
# is an owner-gated call, exactly as `scripts/run_paper_marks.py` documents for
# its own loop. The queue's 14-day retention is what makes that honest: until
# the invocation is wired, nothing is lost, it is only late. See
# `docs/runbooks/ses-bounce-signal.md`.
#
# data.aws_caller_identity.current is declared in ecs.tf and reused below, the
# same way ses_inbound.tf reuses it.

# ── Configuration set ────────────────────────────────────────────────────────
#
# A configuration set is the ONLY way to get per-message event feedback out of
# SES, and it applies only to sends that name it. `auth/mailer.js` passes
# `ConfigurationSetName` from the `SES_CONFIGURATION_SET` environment variable
# (ecs.tf, auth container) — the name below and that env value must be the same
# string or events are published for nothing. That pairing is a guard, not a
# convention: backend/tests/test_ses_event_wiring.py reads both files and fails
# when they drift.
#
# The mailer treats an unset/blank variable as "send without a configuration
# set", i.e. exactly today's behaviour — so this file landing before the
# `terraform apply` that sets the variable degrades to the status quo rather
# than to a broken send path.
resource "aws_sesv2_configuration_set" "mail" {
  configuration_set_name = "${var.project_name}-mail"

  reputation_options {
    # Per-configuration-set bounce/complaint rate metrics in CloudWatch. Free,
    # and the only way to see the rate that governs whether AWS keeps letting
    # us send at all — the account-level number alone cannot tell transactional
    # verification mail apart from anything else we ever send.
    reputation_metrics_enabled = true
  }

  sending_options {
    # Explicit, not inherited: this is the switch AWS itself flips to pause a
    # set that is bouncing badly, so its value belongs in the code rather than
    # in whatever the console last did.
    sending_enabled = true
  }

  tags = { Project = var.project_name }
}

# ── SNS topic SES publishes events to ───────────────────────────────────────

resource "aws_sns_topic" "ses_events" {
  name = "${var.project_name}-ses-events"
  tags = { Project = var.project_name }
}

resource "aws_sns_topic_policy" "ses_events" {
  arn = aws_sns_topic.ses_events.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowSESPublish"
        Effect    = "Allow"
        Principal = { Service = "ses.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.ses_events.arn
        # Same scoping ses_inbound.tf's privacy topic carries: another SES
        # tenant in another account must not be able to publish into a topic
        # whose messages we treat as authoritative about our own users.
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
    ]
  })
}

resource "aws_sesv2_configuration_set_event_destination" "feedback" {
  configuration_set_name = aws_sesv2_configuration_set.mail.configuration_set_name
  event_destination_name = "${var.project_name}-ses-events-sns"

  event_destination {
    enabled = true

    # BOUNCE and COMPLAINT are the two that stamp a user (a permanent bounce
    # means the mailbox does not exist; a complaint means a human told their
    # provider to stop us). REJECT and DELIVERY are carried because they are
    # the control group: REJECT is SES refusing the message outright (virus,
    # blocked content) and DELIVERY is the success case, and without them in
    # the same stream there is no way to tell "no bounce arrived" from "no
    # events are flowing at all". The consumer records neither as a bounce —
    # see `scripts/ses_events.py`'s event table.
    #
    # Deliberately NOT OPEN/CLICK: those need SES to rewrite links and embed a
    # tracking pixel in transactional mail people are asked to trust. No
    # product question here is worth that.
    matching_event_types = ["BOUNCE", "COMPLAINT", "REJECT", "DELIVERY"]

    sns_destination {
      topic_arn = aws_sns_topic.ses_events.arn
    }
  }
}

# ── SQS — the durable buffer ────────────────────────────────────────────────

resource "aws_sqs_queue" "ses_events_dlq" {
  name = "${var.project_name}-ses-events-dlq"

  # Maximum SQS allows. A message here is a bounce the consumer could not
  # parse or could not write; the whole point is that it is still readable
  # days later when somebody notices.
  message_retention_seconds = 1209600

  sqs_managed_sse_enabled = true

  tags = { Project = var.project_name }
}

resource "aws_sqs_queue" "ses_events" {
  name = "${var.project_name}-ses-events"

  # 14 days. This number is what licenses a periodic consumer: a bounce that
  # arrives between drains is late, not lost.
  message_retention_seconds = 1209600

  # Longer than any plausible drain of a single batch (the consumer processes
  # at most a few hundred messages and writes one row each) so a message is
  # never handed to a second reader while the first is still working on it.
  visibility_timeout_seconds = 300

  sqs_managed_sse_enabled = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ses_events_dlq.arn
    # Five attempts, then the DLQ. A message that fails repeatedly is a bug in
    # the parser or a schema change at AWS — it must stop being retried and
    # start being visible, rather than blocking the head of the queue forever.
    maxReceiveCount = 5
  })

  tags = { Project = var.project_name }
}

resource "aws_sqs_queue_policy" "ses_events" {
  queue_url = aws_sqs_queue.ses_events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowSNSDeliveryFromSesEventsTopic"
        Effect    = "Allow"
        Principal = { Service = "sns.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.ses_events.arn
        # Scoped to THIS topic, not to SNS as a service: without the condition
        # any SNS topic in any account could push messages the consumer would
        # treat as SES telling it a user's address is dead.
        Condition = {
          ArnEquals = { "aws:SourceArn" = aws_sns_topic.ses_events.arn }
        }
      },
    ]
  })
}

resource "aws_sns_topic_subscription" "ses_events_sqs" {
  topic_arn = aws_sns_topic.ses_events.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.ses_events.arn

  # Left at the default (false) on purpose: with the SNS envelope intact the
  # message carries its own TopicArn and Timestamp, which is what makes a
  # queue message self-describing when it lands in the DLQ. The consumer
  # unwraps the envelope and also accepts a bare SES event, so flipping this
  # later does not break it (backend/tests/test_ses_bounce_consumer.py covers
  # both shapes).
  raw_message_delivery = false
}

# ── IAM — the consumer's read/delete grant ──────────────────────────────────
#
# On the ECS *task* role (ecs.tf), which is the identity the backend image runs
# under wherever it runs: `aws ecs execute-command` into the running service
# task, a `run-task` override, or a future scheduled task all inherit it. No
# `sqs:SendMessage` — only SNS writes to this queue — and no access to any
# other queue.
resource "aws_iam_role_policy" "ecs_task_ses_events_queue" {
  name = "archimedes-ecs-ses-events-queue"
  role = aws_iam_role.ecs_task.id # ecs.tf

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DrainSesEventQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.ses_events.arn
      },
    ]
  })
}
