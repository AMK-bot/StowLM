"""Layout guards for the Node renderer, which cannot measure browser geometry."""
from pathlib import Path
import re


CSS = Path(__file__).parents[1].joinpath("static/style.css")


def declarations(selector: str) -> dict[str, str]:
    """Read ordered declarations for an exact selector, including mobile rules."""
    result = {}
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(), flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [part.strip() for part in match[1].split(",")]:
            for declaration in match[2].split(";"):
                if ":" in declaration:
                    key, value = declaration.split(":", 1)
                    result[key.strip()] = value.strip()
    return result


def test_phone_selects_and_flex_parents_can_shrink_with_long_instance_ids():
    # At 375 px native selects must not impose the longest option's min-content
    # width on their flex ancestors. Do not hide overflow to mask this defect.
    for selector in ("select", ".scenario", ".story-planner", ".import-controls label"):
        rule = declarations(selector)
        assert rule.get("min-width") == "0", selector
        assert rule.get("max-width") == "100%", selector
    for selector in (".story-planner select", ".import-controls select"):
        rule = declarations(selector)
        assert rule.get("min-width") == "0", selector
        assert rule.get("max-width") == "100%", selector
    assert declarations("select option").get("white-space") == "normal"
    assert declarations("select option").get("overflow-wrap") == "anywhere"


def test_phone_form_layout_stacks_to_the_available_width():
    css = CSS.read_text()
    assert "@media(max-width:600px)" in css
    assert declarations(".intro").get("flex-direction") == "column"
    assert declarations(".scenario").get("width") == "100%"
    assert declarations(".import-controls label").get("display") == "flex"
    assert declarations(".import-controls select").get("margin-left") == "0"
    assert declarations(".transport-buttons").get("flex-wrap") == "wrap"
