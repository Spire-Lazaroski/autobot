"""run_news.py: the news collector's supervisor restarts crashes and stays quiet otherwise."""
import run_bot
import run_news


def _say(lines):
    return lambda msg, level="INFO": lines.append(f"{level} {msg}")


def test_restarts_after_crashes_and_never_gives_up_for_good(monkeypatch, tmp_path):
    """The collector never trades: after 3 restarts in an hour its supervisor waits 15 minutes and tries again."""
    lines, slept = [], []
    t = [0.0]
    monkeypatch.setattr(run_news, "log", _say(lines))
    monkeypatch.setattr(run_news, "OUT_LOG", tmp_path / "out.log")

    def sleep(s):
        slept.append(s)
        t[0] += s
    codes = iter([1, 1, 1, 1, 1, 0])
    code = run_news.supervise(run=lambda: next(codes), sleep=sleep, clock=lambda: t[0])
    assert code == 0
    assert slept == [run_bot.PAUSE_S] * 3 + [run_news.COOLDOWN_S] + [run_bot.PAUSE_S]
    text = " | ".join(lines)
    assert text.count("CRASH: the news collector exited with code 1") == 5
    assert "waiting 15 min, then trying again" in text and "giving up" not in text


def test_already_running_is_quiet(monkeypatch):
    lines = []
    monkeypatch.setattr(run_news, "log", _say(lines))
    assert run_news.supervise(run=lambda: run_bot.EXIT_ALREADY_RUNNING) == run_bot.EXIT_ALREADY_RUNNING
    assert lines == []


def test_collector_exit_code_matches():
    import news_collector
    assert news_collector.EXIT_ALREADY_RUNNING == run_bot.EXIT_ALREADY_RUNNING
