"""Supervisor: restarts only after crashes, at most 3 per hour; quiet on a second copy."""
import run_bot


def test_policy_three_per_hour():
    p = run_bot.RestartPolicy()
    assert [p.allow(t) for t in (0, 10, 20, 30)] == [True, True, True, False]
    assert p.allow(3601)  # the first restart has left the one-hour window


def run_sequence(codes, monkeypatch, tmp_path):
    monkeypatch.setattr(run_bot, "BOT_LOG", tmp_path / "bot.log")
    monkeypatch.setattr(run_bot, "OUT_LOG", tmp_path / "out.log")
    it = iter(codes)
    runs = []
    t = [0.0]

    def run():
        runs.append(1)
        return next(it)

    def sleep(s):
        t[0] += s
    code = run_bot.supervise(run=run, sleep=sleep, clock=lambda: t[0])
    return code, len(runs), (tmp_path / "bot.log").read_text(encoding="utf-8") if (tmp_path / "bot.log").exists() else ""


def test_normal_stop_is_not_restarted(monkeypatch, tmp_path):
    code, runs, log = run_sequence([0], monkeypatch, tmp_path)
    assert code == 0 and runs == 1 and "stopped normally" in log


def test_crash_restarts_then_gives_up(monkeypatch, tmp_path):
    code, runs, log = run_sequence([1, 3, 1, 1, 0], monkeypatch, tmp_path)
    assert runs == 4 and code == 1                   # first run + 3 restarts, then gives up
    assert log.count("CRASH") == 4 and "giving up" in log


def test_crash_then_recovers(monkeypatch, tmp_path):
    code, runs, _ = run_sequence([1, 0], monkeypatch, tmp_path)
    assert code == 0 and runs == 2


def test_second_bot_is_quiet(monkeypatch, tmp_path):
    code, runs, log = run_sequence([4], monkeypatch, tmp_path)
    assert code == 4 and runs == 1 and log == ""
