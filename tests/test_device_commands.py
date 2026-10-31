from tools.device_simulator import AlarmDevice


def response(
    kind="START_ALARM", generation=1, command_id="cmd", mode="ARMED", duration=2000, ttl=5000
):
    return {
        "session_id": "session",
        "mode": mode,
        "mode_revision": 1,
        "command": {
            "command_id": command_id,
            "generation": generation,
            "session_id": "session",
            "boot_id": "boot",
            "mode_revision": 1,
            "type": kind,
            "remaining_ttl_ms": ttl,
            "duration_ms": duration,
        },
    }


def device():
    d = AlarmDevice()
    d.session, d.boot = "session", "boot"
    return d


def test_duplicate_does_not_extend_siren():
    d = device()
    d.apply(response(), 100)
    assert d.apply(response(), 900)["status"] == "duplicate"
    assert d.deadline == 2100
    d.tick(2100)
    assert d.state == "OFF"


def test_stop_beats_late_start_and_expired_start_is_rejected():
    d = device()
    d.apply(response(), 100)
    d.apply(response("STOP_ALARM", 2, "stop"), 200)
    assert d.apply(response(), 300)["status"] == "rejected" and d.state == "OFF"
    assert (
        d.apply(response(generation=3, command_id="late", ttl=100), 400, rtt=200)["status"]
        == "rejected"
    )


def test_test_alarm_survives_normal_disarmed_sync_until_its_deadline():
    d = device()
    d.apply(response("TEST_ALARM", mode="DISARMED", duration=1000), 100)
    state = response(mode="DISARMED")
    state["command"] = None
    d.apply(state, 300)
    assert d.state == "ON"
    d.tick(1100)
    assert d.state == "OFF"
