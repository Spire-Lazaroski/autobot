"""run_news.py: the news collector's supervisor restarts crashes and stays quiet otherwise."""
import run_bot
import run_news


def _say(lines):
    return lambda msg, level="INFO": lines.append(f"{level} {msg}")


def test_restarts_after_a_crash_then_gives_up(monkeypatch, tmp_path):
    lines, slept = [], []
    monkeypatch.setattr(run_news, "log", _say(lines))
    monkeypatch.setattr(run_news, "OUT_LOG", tmp_path / "out.log")
    codes = iter([1, 1, 1, 1])
    t = iter([0, 10, 20, 30])
    code = run_news.supervise(run=lambda: next(codes), sleep=slept.append, clock=lambda: next(t))
    assert code == 1 and slept == [run_bot.PAUSE_S] * 3
    text = "\n".join(lines)
    assert text.count("CRASH: the news collector exited with code 1") == 4
    assert "giving up" in text and "start_news.bat" in text


def test_already_running_is_quiet(monkeypatch):
    lines = []
    monkeypatch.setattr(run_news, "log", _say(lines))
    assert run_news.supervise(run=lambda: run_bot.EXIT_ALREADY_RUNNING) == run_bot.EXIT_ALREADY_RUNNING
    assert lines == []


def test_collector_exit_code_matches():
    import news_collector
    assert news_collector.EXIT_ALREADY_RUNNING == run_bot.EXIT_ALREADY_RUNNING
