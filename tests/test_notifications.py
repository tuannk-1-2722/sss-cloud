import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from security_app.notifications import Notifier, cancel_ineligible, should_notify
from security_app.storage import utcnow


@pytest.mark.parametrize(
    "reason,severity,expected",
    [
        ("unknown_approach", "alarm", True),
        ("unknown_approach", "warning", False),
        ("known_visit", "alarm", False),
        ("unresolved_presence", "warning", False),
        ("capacity_exceeded", "warning", False),
        ("visit_completed", "info", False),
        ("notification_test", "info", False),
        ("other", "alarm", False),
    ],
)
def test_only_policy_confirmed_unknown_can_notify_automatically(reason, severity, expected):
    # Neither identity on a completion nor audible suppression changes eligibility.
    for identity in ("KNOWN", "UNCERTAIN", "UNKNOWN"):
        assert (
            should_notify(
                {"reason": reason, "severity": severity, "identity": identity, "audible": False}
            )
            is expected
        )


@pytest.mark.parametrize("push", [False, True])
def test_delivery_guard_keeps_history_and_only_allows_explicit_system_tests(service, push):
    cases = [
        ("known_visit", "simulator", "sent", "sent"),
        ("known_visit", "simulator", "unknown", "unknown"),
        ("known_visit", "simulator", "failed", "failed"),
        ("unknown_approach", "hardware", "disabled", "disabled"),
        ("notification_test", "system_test", "pending", "pending"),
        ("notification_test", "simulator", "pending", "cancelled"),
        ("unknown_approach", "hardware", "pending", "cancelled"),  # Wrong severity.
    ]
    table = "push_outbox" if push else "outbox"
    with service.store.connection() as db:
        for reason, source, state, _expected in cases:
            eid = str(uuid4())
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
                (eid, eid, reason, "info", utcnow(), source, "{}", None),
            )
            if push:
                db.execute(
                    "INSERT INTO push_outbox(event_id,state,created_at) VALUES(?,?,?)",
                    (eid, state, utcnow()),
                )
            else:
                db.execute(
                    "INSERT INTO outbox(id,event_id,state,created_at) VALUES(?,?,?,?)",
                    (str(uuid4()), eid, state, utcnow()),
                )
        cancel_ineligible(db, push=push)
        rows = db.execute(f"SELECT state FROM {table} ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == [expected for *_, expected in cases]


def test_notification_worker_recovers_after_storage_failure():
    notifier = Notifier.__new__(Notifier)
    notifier.service = SimpleNamespace(notification_error=None)

    class StopAfterTwo:
        loops = 0

        def wait(self, seconds):
            if seconds == 1:
                self.loops += 1
                return self.loops > 2
            return False

    notifier.stop_event = StopAfterTwo()
    calls = []

    def step():
        calls.append(True)
        if len(calls) == 1:
            raise sqlite3.OperationalError("test fault")

    notifier.step = step
    notifier.run()
    assert len(calls) == 2 and notifier.service.notification_error is None
