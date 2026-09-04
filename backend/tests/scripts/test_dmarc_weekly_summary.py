"""The weekly DMARC summary must never let silence read as success (#1504).

WHAT THIS GUARDS. `archimedes.scripts.dmarc_weekly_summary` is the only thing
that ever LOOKS in the reports bucket `infra/dmarc_reports.tf` fills. Its whole
value is that a message arrives every Monday and its absence is noticeable, so
the properties pinned here are the ones whose failure would leave the owner
reading a cheerful email while the domain is being forged, or reading nothing
at all and assuming a quiet week:

  1. **A failing row survives all the way into the message.** The summary is
     built from the same parser the operator CLI uses, and a record whose raw
     SPF passes while its ALIGNED SPF fails is a DMARC failure. If the emailed
     table ever counts that as a pass — or drops the row — the Monday mail
     becomes the evidence for a premature `p=reject`.
     ``test_a_row_that_fails_alignment_is_failing_everywhere_in_the_message``.

  2. **Zero reports still sends, and says so in words.** Not "0 failures".
     ``test_zero_reports_still_sends_a_message_that_says_so``.

  3. **A send failure exits non-zero.** Exit 0 from this job is the only thing
     that claims the owner was told.
     ``test_a_refused_send_exits_four_and_says_nothing_was_sent``.

  4. **A read failure is not "no reports".** Exit 3, and no message at all,
     because a broken S3 path rendered as a quiet week is the precise false
     all-clear this issue exists to end.
     ``test_a_read_failure_exits_three_and_sends_nothing``.

Hermetic: reports come from `tests/dmarc_fixtures` (shared with the parser
suite so both consumers test the same shapes), S3 and SES are hand-written
stubs. No boto3, no credentials, no network.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from archimedes.scripts import dmarc_weekly_summary as job

from tests import dmarc_fixtures as fx

NOW = datetime(2026, 9, 7, 13, 0, tzinfo=UTC)  # a Monday, 13:00 UTC

#: Our own SES egress: aligned DKIM carries it, aligned SPF fails because the
#: envelope sender is in amazonses.com. Healthy, and the row that would be
#: reported as a mass failure by anything reading the wrong column.
OUR_SES_IP = "54.240.8.1"

#: Raw SPF PASSES for the sender's own domain; alignment against our From:
#: FAILS. This is the row that must stay failing everywhere.
FORGER_IP = "203.0.113.77"


# ── Stubs ────────────────────────────────────────────────────────────────────


class StubS3:
    """Just enough S3: a paginator over a dict, and get_object."""

    def __init__(self, objects: dict[str, tuple[bytes, datetime]], *, fail: Exception | None = None):
        self.objects = objects
        self.fail = fail
        self.downloaded: list[str] = []

    def get_paginator(self, operation: str):
        assert operation == "list_objects_v2"
        outer = self

        class _Paginator:
            def paginate(self, Bucket: str, Prefix: str):  # boto3's own kwarg names
                if outer.fail:
                    raise outer.fail
                yield {
                    "Contents": [
                        {"Key": key, "LastModified": stamp}
                        for key, (_, stamp) in outer.objects.items()
                        if key.startswith(Prefix)
                    ]
                }

        return _Paginator()

    def get_object(self, Bucket: str, Key: str):  # boto3's own kwarg names
        self.downloaded.append(Key)
        return {"Body": io.BytesIO(self.objects[Key][0])}


class StubSES:
    """Records what was sent, or refuses like SES does."""

    def __init__(self, *, fail: Exception | None = None, message_id: str | None = "0100018f-msg"):
        self.fail = fail
        self.message_id = message_id
        self.sent: list[dict] = []

    def send_email(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append(kwargs)
        return {"MessageId": self.message_id} if self.message_id else {}


def _stored(xml: bytes, *, days_ago: float, filename: str = "r.zip") -> tuple[bytes, datetime]:
    """One S3 object: the whole MIME message, as the receipt rule stores it."""
    payload = fx.zipped(xml) if filename.endswith(".zip") else fx.gzipped(xml)
    return (fx.as_ses_object(payload, filename), NOW - timedelta(days=days_ago))


def _run(objects, ses=None, argv=None, s3=None):
    ses = ses or StubSES()
    s3 = s3 or StubS3(objects)
    code = job.main(
        argv if argv is not None else ["--bucket", "archimedes-dmarc-reports-1", "--to", "owner@example.com"],
        s3_client=s3,
        ses_client=ses,
        now=NOW,
    )
    return code, ses, s3


def _bodies(ses: StubSES) -> tuple[str, str, str]:
    """(subject, text, html) of the one message sent."""
    assert len(ses.sent) == 1, f"expected exactly one message, got {len(ses.sent)}"
    content = ses.sent[0]["Content"]["Simple"]
    return (
        content["Subject"]["Data"],
        content["Body"]["Text"]["Data"],
        content["Body"]["Html"]["Data"],
    )


# ── Guard 1: a failing row survives into the message ────────────────────────


def test_a_row_that_fails_alignment_is_failing_everywhere_in_the_message():
    """The one misreading that would justify a premature p=reject, end to end.

    `FORGER_IP`'s record has ``auth_results/spf/result = pass`` (raw SPF passes,
    for the sender's OWN domain) and ``policy_evaluated/spf = fail`` (it does
    not align with our From:). DMARC failed. If any layer between the XML and
    the owner's inbox reads the first column, this row disappears from the
    failure count and the Monday mail starts saying the domain is clean.
    """
    xml = fx.aggregate_report(
        records=fx.record(source_ip=OUR_SES_IP, count=42, spf_aligned="fail", spf_auth="fail")
        + fx.record(
            source_ip=FORGER_IP,
            count=3,
            dkim_aligned="fail",
            spf_aligned="fail",
            dkim_auth="none",
            spf_auth="pass",  # raw SPF PASSES — for their domain, not ours
            spf_auth_domain="forwarder.example",
        )
    )
    code, ses, _ = _run({"reports/one": _stored(xml, days_ago=2)})
    assert code == 0
    subject, text, html_body = _bodies(ses)

    assert "FAILED alignment" in subject, f"the verdict must be in the subject line: {subject!r}"
    assert "3 of 45" in subject, f"3 failing of 45 total; got {subject!r}"

    # The plain-text body is render_table's output verbatim, so the operator's
    # table and the emailed table cannot disagree.
    assert "VERDICT: 3 of 45 messages FAILED DMARC alignment" in text
    assert FORGER_IP in text
    # Our own SES egress is NOT a failure: aligned DKIM carries DMARC alone.
    assert f"{OUR_SES_IP} " in text
    assert "0 of 45" not in text

    assert FORGER_IP in html_body
    assert "#fdecea" in html_body, "the failing row must be visibly tinted, not merely sorted first"


def test_our_own_ses_egress_is_not_reported_as_a_failure():
    """Aligned SPF fails for every message we send. Alone, that is healthy.

    A summary that flagged it would report our own production mail as a mass
    forgery every Monday, and the owner would learn to ignore the message —
    which costs the same as not sending one.
    """
    xml = fx.aggregate_report(records=fx.record(source_ip=OUR_SES_IP, count=120, spf_aligned="fail", spf_auth="fail"))
    code, ses, _ = _run({"reports/one": _stored(xml, days_ago=1)})
    assert code == 0
    subject, text, html_body = _bodies(ses)
    assert "all 120 messages aligned" in subject, subject
    assert "FAILED" not in subject
    assert "0 of 120 messages failed DMARC alignment" in text
    assert "#fdecea" not in html_body, "nothing should be tinted as failing on a clean week"


# ── Guard 2: a quiet week still sends, and says what it means ───────────────


def test_zero_reports_still_sends_a_message_that_says_so():
    """Silence must not look like success, and must not look like nothing.

    An empty bucket is indistinguishable from an un-forged domain; a job that
    sends nothing is indistinguishable from a job that stopped running. So the
    message goes out, it names the absence in words, and it points at the
    diagnosis ladder.
    """
    code, ses, _ = _run({})
    assert code == 0, "a quiet week is not a failure — it still has to be reported"
    subject, text, html_body = _bodies(ses)
    assert "NO REPORTS RECEIVED" in subject, subject
    assert "NO REPORTS RECEIVED in the last 7 days." in text
    assert "not a clean bill of health" in text
    assert "docs/runbooks/dmarc-reports.md" in text
    assert "No reports are arriving" in text
    # The failure-count vocabulary must be absent: "0 failures" is the reading
    # this whole path exists to prevent.
    assert "aligned" not in subject
    assert "no reports" in html_body.lower()


def test_objects_that_contain_no_report_are_still_a_quiet_week_not_a_clean_one():
    """An object that is not a report must not be counted as reports parsed."""
    code, ses, _ = _run({"reports/junk": (b"this is not a dmarc report", NOW - timedelta(days=1))})
    assert code == 0
    subject, text, _ = _bodies(ses)
    assert "NO REPORTS RECEIVED" in subject
    assert "1 object(s) in the window" in text


# ── Guard 3: a refused send is never a success ──────────────────────────────


def test_a_refused_send_exits_four_and_says_nothing_was_sent(capsys):
    """Exit 0 is the only thing that claims the owner was told."""
    ses = StubSES(fail=RuntimeError("MessageRejected: Email address is not verified"))
    code, ses, _ = _run({"reports/one": _stored(fx.SAMPLE_XML, days_ago=2)}, ses=ses)
    assert code == 4, "a send failure must be distinguishable from a successful summary"
    assert ses.sent == []
    assert "SES refused the summary" in capsys.readouterr().err


def test_a_response_with_no_message_id_is_not_a_send(capsys):
    """SES answering without a MessageId is not acceptance.

    Treating it as one would be the same silent success as swallowing the
    exception in the test above, arriving through a different door.
    """
    ses = StubSES(message_id=None)
    code, _, _ = _run({"reports/one": _stored(fx.SAMPLE_XML, days_ago=2)}, ses=ses)
    assert code == 4
    assert "no MessageId" in capsys.readouterr().err


# ── Guard 4: "could not look" is not "found nothing" ────────────────────────


def test_a_read_failure_exits_three_and_sends_nothing(capsys):
    """A broken S3 path reported as a quiet week is the false all-clear."""
    s3 = StubS3({}, fail=RuntimeError("AccessDenied: not authorized to perform s3:ListBucket"))
    ses = StubSES()
    code = job.main(
        ["--bucket", "b", "--to", "owner@example.com"],
        s3_client=s3,
        ses_client=ses,
        now=NOW,
    )
    assert code == 3, "a read failure must be distinct from both success and a send failure"
    assert ses.sent == [], "nothing may be mailed when the bucket could not be read"
    err = capsys.readouterr().err
    assert "could not read s3://b/reports/" in err
    assert "NO REPORTS" not in err


# ── Windowing and the week-on-week diff ─────────────────────────────────────


def test_only_the_last_seven_days_are_summarised():
    """The window is what makes "this week" mean anything."""
    inside = fx.aggregate_report(records=fx.record(source_ip=OUR_SES_IP, count=5))
    outside = fx.aggregate_report(records=fx.record(source_ip="198.51.100.9", count=9999))
    code, ses, _ = _run(
        {
            "reports/recent": _stored(inside, days_ago=3),
            "reports/ancient": _stored(outside, days_ago=40),
        }
    )
    assert code == 0
    _, text, _ = _bodies(ses)
    assert "198.51.100.9" not in text, "an object older than both windows must not be listed at all"
    assert "9999" not in text


def test_a_source_absent_last_week_is_announced_as_new():
    """The one line worth a Monday morning."""
    last_week = fx.aggregate_report(records=fx.record(source_ip=OUR_SES_IP, count=50))
    this_week = fx.aggregate_report(
        records=fx.record(source_ip=OUR_SES_IP, count=60)
        + fx.record(source_ip=FORGER_IP, count=4, dkim_aligned="fail", spf_aligned="fail")
    )
    code, ses, _ = _run(
        {
            "reports/prev": _stored(last_week, days_ago=10),
            "reports/this": _stored(this_week, days_ago=1),
        }
    )
    assert code == 0
    subject, text, _ = _bodies(ses)
    assert "1 new source(s)" in subject, subject
    assert f"NEW sources (1): {FORGER_IP} (FAILING)" in text
    assert OUR_SES_IP not in text.split("CHANGES SINCE")[1], "a source seen last week is not new"


def test_the_previous_window_is_only_the_week_before_it():
    """The comparison window is one window long, not "everything older"."""
    code, ses, _ = _run(
        {
            "reports/very-old": _stored(
                fx.aggregate_report(records=fx.record(source_ip="192.0.2.4", count=1)), days_ago=30
            ),
            "reports/this": _stored(fx.aggregate_report(records=fx.record(source_ip=OUR_SES_IP, count=2)), days_ago=1),
        }
    )
    assert code == 0
    _, text, _ = _bodies(ses)
    # Nothing landed in the previous window, so there is nothing to compare
    # against — and the summary must say that rather than call every source new.
    assert "nothing to compare against" in text
    assert "192.0.2.4" not in text


def test_no_reports_last_week_does_not_make_every_source_new():
    """A first week of collection must not read as a week of new forgeries."""
    code, ses, _ = _run({"reports/this": _stored(fx.SAMPLE_XML, days_ago=1)})
    assert code == 0
    subject, text, _ = _bodies(ses)
    assert "new source(s)" not in subject, subject
    assert "nothing to compare against" in text


# ── The message itself ──────────────────────────────────────────────────────


def test_report_content_is_escaped_before_it_reaches_the_html_body():
    """Every cell arrives from the public internet.

    `dmarc-reports@` is an address published in DNS; anyone can mail it a
    hand-written "report". A crafted `org_name` that landed unescaped in the
    HTML body could rewrite the summary the owner reads to decide whether the
    domain is being forged.
    """
    # XML-escaped in the document (a report with raw `<` in a text node is not
    # well-formed XML and never reaches the renderer at all), so what the
    # parser hands the renderer is the literal string `<script>alert(1)</script>`.
    hostile = fx.aggregate_report(
        org="&lt;script&gt;alert(1)&lt;/script&gt;",
        records=fx.record(source_ip="192.0.2.1", count=1, dkim_aligned="fail", spf_aligned="fail"),
    )
    code, ses, _ = _run({"reports/one": _stored(hostile, days_ago=1)})
    assert code == 0
    _, _, html_body = _bodies(ses)
    assert "<script>" not in html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_body


def test_the_message_is_sent_from_the_verified_identity_to_the_configured_address():
    code, ses, _ = _run({"reports/one": _stored(fx.SAMPLE_XML, days_ago=1)})
    assert code == 0
    sent = ses.sent[0]
    assert sent["FromEmailAddress"] == "no-reply@archimedes-arc.com"
    assert sent["Destination"]["ToAddresses"] == ["owner@example.com"]
    # Both bodies, always: the text one is the operator's own table, the HTML
    # one is what makes it readable on a phone.
    body = sent["Content"]["Simple"]["Body"]
    assert body["Text"]["Data"].strip()
    assert body["Html"]["Data"].strip()


def test_several_recipients_may_be_named():
    code, ses, _ = _run(
        {"reports/one": _stored(fx.SAMPLE_XML, days_ago=1)},
        argv=["--bucket", "b", "--to", "one@example.com, two@example.com"],
    )
    assert code == 0
    assert ses.sent[0]["Destination"]["ToAddresses"] == ["one@example.com", "two@example.com"]


# ── Configuration refusals ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--to", "owner@example.com"], "no reports bucket"),
        (["--bucket", "b"], "no recipient"),
        (["--bucket", "b", "--to", "owner@example.com", "--days", "0"], "--days must be positive"),
    ],
)
def test_a_misconfigured_job_exits_two_rather_than_reporting_a_quiet_week(argv, needle, capsys, monkeypatch):
    """Exit 2, and nothing sent. A summary addressed to nobody runs forever and
    tells no one, which is the state this job exists to end."""
    monkeypatch.delenv(job.BUCKET_ENV, raising=False)
    monkeypatch.delenv(job.RECIPIENT_ENV, raising=False)
    ses = StubSES()
    code = job.main(argv, s3_client=StubS3({}), ses_client=ses, now=NOW)
    assert code == 2
    assert ses.sent == []
    assert needle in capsys.readouterr().err


def test_dry_run_reads_the_bucket_and_sends_nothing(capsys):
    ses = StubSES()
    code = job.main(
        ["--bucket", "b", "--dry-run"],
        s3_client=StubS3({"reports/one": _stored(fx.SAMPLE_XML, days_ago=1)}),
        ses_client=ses,
        now=NOW,
    )
    assert code == 0
    assert ses.sent == [], "--dry-run must not send"
    out = capsys.readouterr().out
    assert "Subject: DMARC weekly" in out
    assert "dry run — nothing sent" in out


# ── The import surface the scheduled task depends on ────────────────────────


def test_the_job_needs_no_database():
    """`infra/dmarc_reports.tf` gives this task NO secrets — no DATABASE_URL.

    That is only safe while the import path stays clear of
    `archimedes.services`, whose package __init__ re-exports the generation
    pipeline and the redis state module and therefore wants a database URL at
    import time. An import added here would not fail a unit test on its own —
    it would crash-loop a Monday-morning Fargate task nobody is watching, and
    the first symptom would be a summary that stopped arriving.
    """
    source = (job.__file__ or "").strip()
    assert source.endswith("dmarc_weekly_summary.py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "archimedes.services" not in text, (
        "importing archimedes.services drags the application in and needs DATABASE_URL at import "
        "time; the scheduled task in infra/dmarc_reports.tf deliberately has no secrets block"
    )
    assert "from archimedes.scripts.dmarc_reports import" in text, (
        "the summary must read reports with the same parser the operator CLI uses"
    )
