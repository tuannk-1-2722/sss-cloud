from types import SimpleNamespace

from tools.replay import replay_latest


def test_slow_inference_replaces_pending_with_latest_and_preserves_observation_times():
    frames = [SimpleNamespace(time_ms=t, received_ms=t) for t in range(0, 1001, 100)]
    called = []

    def analyze(index):
        called.append(index)
        return [index], 500

    result = replay_latest(frames, analyze, interval_ms=300, max_age_ms=3000)
    assert called == [0, 5, 10]
    assert result["replaced"] == 8 and result["stale"] == 0
    assert [r["finished_ms"] for r in result["outputs"]] == [500, 1000, 1500]


def test_inference_finishing_after_freshness_limit_cannot_publish():
    frames = [SimpleNamespace(time_ms=0, received_ms=100)]
    result = replay_latest(
        frames, lambda _: (["unsafe-old-result"], 3000), interval_ms=300, max_age_ms=3000
    )
    assert result["outputs"] == [] and result["stale"] == 1
