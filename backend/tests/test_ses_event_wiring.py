"""The SES bounce signal is a chain, and every link is invisible when broken (#1804).

    auth/mailer.js  ──ConfigurationSetName──▶  aws_sesv2_configuration_set.mail
                                                       │
                                          event destination (BOUNCE, COMPLAINT)
                                                       ▼
                                              aws_sns_topic.ses_events
                                                       ▼
                                              aws_sqs_queue.ses_events
                                                       ▼
                                  archimedes.scripts.ses_events drain

Every one of those links fails SILENTLY. A send that names no configuration
set is a perfectly successful send that publishes no event. A configuration
set whose name does not match the one the mailer sends with publishes events
for nobody. An SNS topic with no SQS subscriber drops each notification on the
floor. A task role without the queue grant makes the drain raise AccessDenied
where nobody is watching. In every case mail still goes out, no alarm fires,
and the product goes back to being unable to tell a dead address from an
impatient human — the exact state #1804 is about.

``terraform validate`` catches none of it: a configuration set nobody
references, an event destination with an empty ``matching_event_types``, and a
mailer that sends without a set are all syntactically valid.

These tests read the repo's own files as text. Hermetic by construction — no
AWS, no terraform binary, no network, no env vars.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SES_EVENTS_TF = REPO_ROOT / "infra" / "ses_events.tf"
ECS_TF = REPO_ROOT / "infra" / "ecs.tf"
MAILER_JS = REPO_ROOT / "auth" / "mailer.js"
OUTPUTS_TF = REPO_ROOT / "infra" / "outputs.tf"

#: The terraform expression the auth container's SES_CONFIGURATION_SET value
#: must be, and the one the IAM configuration-set ARN must interpolate. Using
#: the resource attribute rather than a copy of the string is what makes the
#: "name in terraform == name the mailer sends with" guard structural: there is
#: one name, in one place, and the other two files reference it.
CONFIG_SET_REF = "aws_sesv2_configuration_set.mail.configuration_set_name"

#: The env var name is the contract between terraform and auth/mailer.js.
CONFIG_SET_ENV = "SES_CONFIGURATION_SET"
QUEUE_URL_ENV = "SES_EVENTS_QUEUE_URL"


def _read(path: Path) -> str:
    assert path.exists(), f"{path} is missing — the SES feedback loop has no push half without it"
    return path.read_text(encoding="utf-8")


def _strip_comments(hcl: str) -> str:
    """Drop `#` comment lines so a mention in prose cannot satisfy a guard."""
    return "\n".join(line for line in hcl.splitlines() if not line.lstrip().startswith("#"))


# ─────────────────── the mailer actually names the set ──────────────────


def test_mailer_sends_with_the_configuration_set_from_the_environment():
    """No ConfigurationSetName on SendEmail == no bounce events, ever."""
    mailer = _read(MAILER_JS)
    assert f"env.{CONFIG_SET_ENV}" in mailer, (
        f"auth/mailer.js must read {CONFIG_SET_ENV} — without it every send is invisible to the feedback loop"
    )
    assert "ConfigurationSetName" in mailer, (
        "auth/mailer.js must pass ConfigurationSetName to SendEmailCommand; a configuration set that no "
        "send names publishes events for nothing"
    )
    # The property must be inside the SES SendEmailCommand payload, not merely
    # mentioned: everything between `new ses.SendEmailCommand({` and the
    # mailer's console branch.
    send_command = mailer.split("new ses.SendEmailCommand(", 1)[1].split("kind: 'console'", 1)[0]
    assert "ConfigurationSetName" in send_command


def test_the_set_name_is_never_hardcoded_in_the_mailer():
    """One name, one place. The mailer reads it; terraform owns it."""
    mailer = _read(MAILER_JS)
    assert "archimedes-mail" not in mailer, (
        "auth/mailer.js must not carry a literal configuration set name — it comes from "
        f"{CONFIG_SET_ENV}, whose value infra/ecs.tf takes straight off the terraform resource"
    )


def test_a_blank_configuration_set_falls_back_to_todays_behaviour():
    """Landing this before the terraform apply must not break verification mail.

    The set does not exist until the owner applies; until then the variable is
    unset. Sending an empty ConfigurationSetName would be rejected by SES, so
    the property has to be OMITTED rather than blank.
    """
    mailer = _read(MAILER_JS)
    assert "configurationSet ? { ConfigurationSetName: configurationSet } : {}" in mailer


# ──────────────── terraform and the mailer agree on the name ────────────


def test_the_auth_container_is_given_the_configuration_sets_own_name():
    """THE guard #1804 asks for: the name in terraform == the name the mailer sends with.

    Enforced structurally rather than by string comparison — the env value is
    the resource attribute itself, so the two cannot be edited apart.
    """
    ecs = _strip_comments(_read(ECS_TF))
    pattern = re.compile(
        r'\{\s*name\s*=\s*"' + CONFIG_SET_ENV + r'"\s*,\s*value\s*=\s*' + re.escape(CONFIG_SET_REF) + r"\s*\}"
    )
    assert pattern.search(ecs), (
        f"infra/ecs.tf's auth container must set {CONFIG_SET_ENV} = {CONFIG_SET_REF}. A hardcoded literal "
        "here is exactly how the two names drift, and a drifted name publishes events for nobody while "
        "mail keeps flowing."
    )


def test_the_task_role_may_send_with_that_configuration_set():
    """Naming a set on SendEmail needs IAM on the SET as well as the identity.

    Without the configuration-set ARN in the same statement, turning the signal
    on turns verification mail OFF — every send fails AccessDeniedException.
    """
    ecs = _strip_comments(_read(ECS_TF))
    assert f"configuration-set/${{{CONFIG_SET_REF}}}" in ecs, (
        "infra/ecs.tf's ecs_task_ses_send policy must also grant the configuration-set ARN"
    )


def test_the_backend_container_knows_which_queue_to_drain():
    ecs = _strip_comments(_read(ECS_TF))
    pattern = re.compile(
        r'\{\s*name\s*=\s*"'
        + QUEUE_URL_ENV
        + r'"\s*,\s*value\s*=\s*'
        + re.escape("aws_sqs_queue.ses_events.id")
        + r"\s*\}"
    )
    assert pattern.search(ecs), (
        f"infra/ecs.tf's backend container must set {QUEUE_URL_ENV} = aws_sqs_queue.ses_events.id — "
        "`ses_events drain` reads it, and hardcoding a queue URL anywhere else is how it ends up "
        "draining the wrong account's queue"
    )


def test_the_task_role_can_read_and_delete_from_the_queue():
    tf = _strip_comments(_read(SES_EVENTS_TF))
    grant = tf.split('resource "aws_iam_role_policy" "ecs_task_ses_events_queue"', 1)
    assert len(grant) == 2, "no IAM grant for the SES event queue — the drain would raise AccessDenied"
    body = grant[1]
    for action in ("sqs:ReceiveMessage", "sqs:DeleteMessage"):
        assert action in body, f"the queue grant must allow {action}"
    assert "sqs:SendMessage" not in body, (
        "only SNS writes to this queue; granting the task role SendMessage would let application code "
        "manufacture bounce events about its own users"
    )


# ───────────────────── the events actually reach the queue ──────────────


def test_the_event_destination_carries_bounce_and_complaint():
    tf = _strip_comments(_read(SES_EVENTS_TF))
    match = re.search(r"matching_event_types\s*=\s*\[([^\]]*)\]", tf)
    assert match, "no matching_event_types — an event destination that matches nothing is not a signal"
    types = {value.strip().strip('"') for value in match.group(1).split(",") if value.strip()}
    assert {"BOUNCE", "COMPLAINT"} <= types, f"BOUNCE and COMPLAINT are the two that stamp a user; got {sorted(types)}"
    # Not an accident of scope creep: link tracking rewrites URLs and embeds a
    # pixel in transactional mail people are asked to trust.
    assert not ({"OPEN", "CLICK"} & types), "no open/click tracking on verification mail"


def test_the_destination_publishes_to_the_topic_the_queue_subscribes_to():
    """SNS with no subscriber DROPS notifications — the queue is the durability."""
    tf = _strip_comments(_read(SES_EVENTS_TF))
    assert "sns_destination {" in tf
    assert "topic_arn = aws_sns_topic.ses_events.arn" in tf
    subscription = tf.split('resource "aws_sns_topic_subscription" "ses_events_sqs"', 1)
    assert len(subscription) == 2, "the topic has no SQS subscription — every bounce would be dropped"
    body = subscription[1].split("\n}", 1)[0]
    assert 'protocol  = "sqs"' in body or 'protocol = "sqs"' in body
    assert "endpoint  = aws_sqs_queue.ses_events.arn" in body or "endpoint = aws_sqs_queue.ses_events.arn" in body


def test_only_our_own_ses_can_publish_and_only_our_topic_can_enqueue():
    """Both hops are scoped: these messages are treated as truth about users."""
    tf = _strip_comments(_read(SES_EVENTS_TF))
    assert '"AWS:SourceAccount" = data.aws_caller_identity.current.account_id' in tf, (
        "the SNS topic policy must scope ses.amazonaws.com to this account"
    )
    assert 'ArnEquals = { "aws:SourceArn" = aws_sns_topic.ses_events.arn }' in tf, (
        "the queue policy must scope sns.amazonaws.com to this topic, not to SNS as a whole"
    )


def test_the_queue_is_durable_and_has_a_dead_letter_queue():
    """The retention is what makes a PERIODIC consumer honest rather than lossy."""
    tf = _strip_comments(_read(SES_EVENTS_TF))
    retentions = re.findall(r"message_retention_seconds\s*=\s*(\d+)", tf)
    assert retentions, "no message_retention_seconds — a bounce that arrives between drains would expire early"
    assert all(int(value) == 1209600 for value in retentions), (
        f"both queues must hold the SQS maximum of 14 days; got {retentions}"
    )
    assert "deadLetterTargetArn = aws_sqs_queue.ses_events_dlq.arn" in tf, (
        "without a DLQ an unparseable message is retried forever at the head of the queue"
    )
    assert "sqs_managed_sse_enabled = true" in tf


def test_the_queue_url_is_exported_for_the_operator():
    outputs = _strip_comments(_read(OUTPUTS_TF))
    assert 'output "ses_events_queue_url"' in outputs
    assert 'output "ses_configuration_set_name"' in outputs
