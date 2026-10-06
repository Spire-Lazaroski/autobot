"""Agreement test: counting agreeing strategies and agreeing families."""
import pandas as pd

from autobot import agreement_study as ag


def test_count_and_family_agreement():
    idx = range(4)
    sigs = {"a": pd.Series([1, 1, 0, -1], index=idx), "b": pd.Series([1, 0, 0, -1], index=idx),
            "c": pd.Series([1, 1, -1, -1], index=idx)}
    fam = {"a": "trend", "b": "trend", "c": "mean reversion"}
    assert ag.combine(sigs, fam, "count", 2, False).tolist() == [1, 1, 0, 0]
    assert ag.combine(sigs, fam, "count", 3, True).tolist() == [1, 0, 0, -1]
    # two families agree only when both trend and reversion are long
    assert ag.combine(sigs, fam, "families", 2, False).tolist() == [1, 1, 0, 0]
    assert ag.combine(sigs, fam, "families", 2, True).tolist() == [1, 1, 0, -1]
