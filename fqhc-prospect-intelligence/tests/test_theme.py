"""The stylesheet's own invariants.

Not a test of how anything looks — that is a judgement — but of the rules that
make the two themes work at all, each of which has a failure mode that is
invisible until somebody opens the app at night or prints a profile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "app/static/css/brand.css").read_text()
BASE = (Path(__file__).resolve().parent.parent / "app/templates/base.html").read_text()

BRAND_COLORS = {
    "--allstar-blue": "#0094bb",
    "--allstar-purple": "#524fa2",
    "--allstar-pink": "#cf118c",
    "--allstar-orange": "#f58220",
    "--allstar-yellow": "#ffd503",
    "--allstar-green": "#6fc055",
}


def tokens(pattern: str) -> dict[str, str]:
    match = re.search(pattern, CSS, re.S | re.M)
    assert match, f"no block matching {pattern}"
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", match.group(1))
    }


@pytest.mark.parametrize("token, value", BRAND_COLORS.items())
def test_the_brand_colors_are_still_exactly_the_brand_colors(token, value) -> None:
    """The style guide gives these as hex values. They are not up for tuning."""
    assert f"{token}: {value};" in CSS


def test_the_star_band_still_has_all_six_colors() -> None:
    for name in BRAND_COLORS:
        assert f"background: var({name});" in CSS


def test_both_dark_blocks_define_exactly_the_same_tokens() -> None:
    """The media query and the attribute block are two doors to one room.

    A token defined in one and not the other means the toggle and the system
    setting produce different pages, which nobody would notice until they used
    the one that was missing something.
    """
    media = tokens(r':root:not\(\[data-theme="light"\]\)\s*\{(.*?)\n  \}')
    attribute = tokens(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}')

    assert set(media) == set(attribute)
    for name, value in media.items():
        assert " ".join(value.split()) == " ".join(attribute[name].split()), name


def test_every_theme_token_has_a_light_definition() -> None:
    """A token defined only inside a dark block is undefined in daylight."""
    light = tokens(r"^:root \{(.*?)\n\}")
    dark = tokens(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}')
    missing = set(dark) - set(light) - {"color-scheme"}
    assert not missing, f"defined only in dark: {sorted(missing)}"


def test_the_dark_block_is_guarded_so_an_explicit_light_choice_wins() -> None:
    """Without :not([data-theme="light"]) the toggle only works one way."""
    assert ':root:not([data-theme="light"])' in CSS


def test_printing_forces_a_white_ground_whatever_the_screen_was() -> None:
    match = re.search(r"@media print \{(.*?)\n\}\n", CSS, re.S)
    assert match
    block = match.group(1)
    assert '[data-theme="dark"]' in block
    assert "--ground: #ffffff;" in block
    assert "--ink: #000000;" in block
    # A pinned header repeats itself down a printed page.
    assert "position: static;" in block


def test_no_component_hardcodes_a_color_that_cannot_flip() -> None:
    """A literal hex outside the token blocks is a light-mode assumption.

    Every one of these was a real bug once: pills whose text stayed dark on a
    dark tint, and button hovers that got darker instead of lighter.
    """
    body = CSS.split("Base\n   ---", 1)[1]
    literals = [
        line.strip()
        for line in body.splitlines()
        if re.search(r"#[0-9a-fA-F]{3,8}\b", line)
        and "rgba(" not in line
        and not line.strip().startswith(("/*", "*"))
        # A custom-property definition is where literals belong -- including
        # the print block's, since paper is white in either theme. What must
        # not carry a literal is a component rule.
        and not line.strip().startswith("--")
    ]
    assert not literals, "hardcoded colors outside the theme blocks: " + "; ".join(
        literals
    )


def test_the_theme_is_applied_before_the_stylesheet_loads() -> None:
    """Otherwise a saved dark preference flashes white on every page load."""
    head_script = BASE.index("fqhc-theme")
    stylesheet = BASE.index("css/brand.css")
    assert head_script < stylesheet


def test_the_theme_control_is_hidden_without_javascript() -> None:
    """A switch that cannot switch anything is worse than no switch."""
    assert ".theme {\n  display: none;" in CSS
    assert ".js .theme { display: inline-flex; }" in CSS
    assert 'classList.add("js")' in BASE


def test_reading_the_saved_theme_survives_storage_being_unavailable() -> None:
    """localStorage throws outright in some privacy modes."""
    script = BASE[BASE.index("fqhc-theme") - 400 : BASE.index("css/brand.css")]
    assert "try {" in script and "catch" in script
