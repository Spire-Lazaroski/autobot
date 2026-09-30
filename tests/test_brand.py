"""Brand tokens: exact match with BRAND.md, generated files in sync, text colours pass WCAG AA."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THEME = (ROOT / "web/src/theme.ts").read_text(encoding="utf-8")
BRAND_JSON = json.loads((ROOT / "autobot/assets/brand.json").read_text(encoding="utf-8"))


def _block(name):
    body = re.search(rf"export const {name} = \{{(.*?)\}} as const;", THEME, re.S).group(1)
    return dict(re.findall(r'"?([\w-]+)"?:\s*"(#[0-9a-fA-F]{6})"', body))


def _lum(hex_):
    rgb = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b="#ffffff"):
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_matches_brand_md():
    md = ROOT / "branding/BRAND.md"
    if not md.exists():
        pytest.skip("branding/BRAND.md not present (it is git-ignored)")
    table = dict(re.findall(r"\|\s*--([\w-]+)\s*\|\s*(#[0-9a-fA-F]{6})\s*\|", md.read_text(encoding="utf-8")))
    assert table, "could not read the token table"
    assert _block("brand") == {k: v.lower() for k, v in table.items()}


def test_generated_files_in_sync():
    assert BRAND_JSON["brand"] == _block("brand")
    assert BRAND_JSON["extra"] == _block("extra")
    web_css = (ROOT / "web/src/brand.css").read_text(encoding="utf-8")
    assets_css = (ROOT / "autobot/assets/brand.css").read_text(encoding="utf-8")
    assert web_css == assets_css
    for k, v in {**_block("brand"), **_block("extra")}.items():
        assert f"--{k}:{v};" in web_css, k


@pytest.mark.parametrize("token", ["profit", "loss", "warn-ink", "ink-faint"])
def test_text_colours_pass_aa_on_white(token):
    assert contrast(_block("extra")[token]) >= 4.5


@pytest.mark.parametrize("token", ["ink", "ink-soft", "navy"])
def test_brand_text_colours_pass_aa(token):
    assert contrast(_block("brand")[token]) >= 4.5


def test_gold_is_not_a_text_colour():
    # documents WHY the app never uses gold as text on white
    assert contrast(_block("brand")["gold"]) < 3


def _hue(hex_):
    import colorsys
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def test_profit_loss_distinct_from_gold_amber():
    b, e = _block("brand"), _block("extra")
    for pl in (e["profit"], e["loss"]):
        for acc in (b["gold"], b["amber"]):
            d = abs(_hue(pl) - _hue(acc))
            assert min(d, 360 - d) >= 25, (pl, acc, round(d))


def test_no_gold_text_in_web_source():
    offenders = []
    for f in (ROOT / "web/src").rglob("*.tsx"):
        s = f.read_text(encoding="utf-8")
        if re.search(r"text-gold|color:\s*[\"']?(var\(--gold\)|#e8b23d)", s):
            offenders.append(f.name)
    assert not offenders
