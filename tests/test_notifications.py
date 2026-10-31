import sqlite3
from types import SimpleNamespace

from security_app.notifications import Notifier


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
