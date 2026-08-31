import pytest

from agents.dist.support_draft_service import approve_and_send, reject_draft


class FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self._filters = []
        self._payload = None
    def select(self, *a, **k):
        return self
    def eq(self, key, value):
        self._filters.append((key, value))
        return self
    def maybe_single(self):
        self._single = True
        return self
    def update(self, row):
        self._payload = row
        return self
    def insert(self, row):
        self._payload = row
        return self
    def _matching(self):
        rows = self.store.data.get(self.table_name, [])
        for k, v in self._filters:
            rows = [r for r in rows if r.get(k) == v]
        return rows
    def execute(self):
        if self._payload is not None:
            matched = self._matching()
            for r in matched:
                r.update(self._payload)
            self.store.executed.append((self.table_name, self._payload))
            return type("R", (), {"data": matched})()
        rows = self._matching()
        if getattr(self, "_single", False):
            return type("R", (), {"data": rows[0] if rows else None})()
        return type("R", (), {"data": rows})()


class FakeDB:
    def __init__(self, data):
        self.data = data
        self.executed = []
    def table(self, name):
        return FakeQuery(self, name)


def _seed():
    return {
        "mse_support_drafts": [{"id": "d1", "ticket_id": "t1", "draft_body": "Original draft", "status": "pending", "confidence": 0.9, "sources": {}}],
        "mse_support_tickets": [{"id": "t1", "product_id": "p1", "status": "drafted", "first_response_at": None}],
        "audit_log": [],
    }


def test_approve_and_send_sends_real_email_and_updates_both_rows():
    db = FakeDB(_seed())
    sent = []

    class FakeResendClient:
        class Emails:
            @staticmethod
            def send(payload):
                sent.append(payload)

    result = approve_and_send(
        "d1", approved_by="kelvin", to_email="customer@example.com",
        subject="Re: your question", supabase_client=db, resend_client=FakeResendClient(),
    )
    assert result["status"] == "approved"
    assert sent[0]["to"] == "customer@example.com"
    assert sent[0]["text"] == "Original draft"
    ticket = db.data["mse_support_tickets"][0]
    assert ticket["status"] == "answered"
    assert ticket["first_response_at"] is not None


def test_approve_with_edit_marks_status_edited_and_sends_final_body():
    db = FakeDB(_seed())
    sent = []

    class FakeResendClient:
        class Emails:
            @staticmethod
            def send(payload):
                sent.append(payload)

    result = approve_and_send(
        "d1", approved_by="kelvin", to_email="c@example.com", final_body="Edited reply text",
        supabase_client=db, resend_client=FakeResendClient(),
    )
    assert result["status"] == "edited"
    assert sent[0]["text"] == "Edited reply text"


def test_cannot_send_an_already_sent_draft():
    db = FakeDB(_seed())
    db.data["mse_support_drafts"][0]["status"] = "approved"

    class FakeResendClient:
        class Emails:
            @staticmethod
            def send(payload):
                raise AssertionError("should never be called")

    with pytest.raises(ValueError, match="already"):
        approve_and_send("d1", approved_by="kelvin", to_email="c@example.com", supabase_client=db, resend_client=FakeResendClient())


def test_send_failure_is_audited_and_reraised():
    db = FakeDB(_seed())

    class FailingResendClient:
        class Emails:
            @staticmethod
            def send(payload):
                raise RuntimeError("Resend API down")

    with pytest.raises(RuntimeError, match="send failed"):
        approve_and_send("d1", approved_by="kelvin", to_email="c@example.com", supabase_client=db, resend_client=FailingResendClient())

    audit_inserts = [p for t, p in db.executed if t == "audit_log"]
    assert len(audit_inserts) == 1
    assert audit_inserts[0]["outcome"] == "lose"
    assert db.data["mse_support_drafts"][0]["status"] == "pending"  # never marked sent


def test_reject_draft_marks_rejected():
    db = FakeDB(_seed())
    result = reject_draft("d1", approved_by="kelvin", supabase_client=db)
    assert result["status"] == "rejected"


def test_reject_nonexistent_draft_raises():
    db = FakeDB(_seed())
    with pytest.raises(ValueError, match="no pending draft"):
        reject_draft("missing", approved_by="kelvin", supabase_client=db)
