"""Unit tests for the HTML sanitizer (CLAUDE.md §14, docs/security §4). No network."""

from __future__ import annotations

import pytest

from atlascore.security.sanitization import strip_html

pytestmark = pytest.mark.unit


def test_script_content_is_dropped() -> None:
    out = strip_html("<p>safe</p><script>steal(document.cookie)</script>")
    assert "safe" in out
    assert "steal" not in out
    assert "cookie" not in out


def test_style_content_is_dropped() -> None:
    out = strip_html("<style>.x{color:red}</style><p>visible</p>")
    assert out == "visible"


def test_inline_event_handlers_are_neutralized() -> None:
    # Only text survives; the onclick attribute (and its payload) never appear.
    out = strip_html('<p onclick="evil()">click me</p>')
    assert out == "click me"
    assert "evil" not in out


def test_unsafe_scheme_links_leave_no_href() -> None:
    out = strip_html('<a href="javascript:alert(1)">link text</a>')
    assert out == "link text"
    assert "javascript" not in out
    assert "alert" not in out


def test_iframe_object_form_dropped() -> None:
    out = strip_html(
        '<iframe src="http://evil.test"></iframe>'
        "<object data='x'></object>"
        "<form><input value='secret'></form>"
        "<p>kept</p>"
    )
    assert out == "kept"
    assert "evil" not in out
    assert "secret" not in out


def test_entities_are_decoded() -> None:
    assert strip_html("<p>AT&amp;T &lt;tag&gt; caf&eacute; &raquo;</p>") == "AT&T <tag> café »"


def test_block_tags_become_newlines() -> None:
    assert strip_html("<p>one</p><p>two</p>") == "one\ntwo"
    assert strip_html("a<br>b") == "a\nb"
    assert strip_html("<li>x</li><li>y</li>") == "x\ny"


def test_whitespace_is_collapsed() -> None:
    assert strip_html("<p>  lots   of\t\tspace  </p>") == "lots of space"


def test_plain_text_passthrough() -> None:
    assert strip_html("just plain text") == "just plain text"


def test_empty_and_none() -> None:
    assert strip_html(None) == ""
    assert strip_html("") == ""
    assert strip_html("   \n  ") == ""


def test_malformed_html_does_not_crash() -> None:
    # Unclosed/garbage tags are tolerated (lenient parser); never executed.
    out = strip_html("<p>open <b>bold <script>x()</script")
    assert "open" in out
    assert "x()" not in out


# --- Adversarial regressions (review findings 2 & 3): a real HTML5 tokenizer must not leak
#     active content when a drop-tag's START tag is malformed or its CLOSE tag is stray. ---


def test_unterminated_quote_in_script_start_tag_does_not_leak() -> None:
    # html.parser mis-tokenizes this as DATA and would leak the whole element; nh3 does not.
    out = strip_html(
        '<p>Real article text.</p><script src="x onerror=alert(1)>maliciousCode()</script>'
    )
    assert "Real article text." in out
    assert "script" not in out.lower()
    assert "maliciousCode" not in out
    assert "onerror" not in out


def test_unterminated_quote_in_style_start_tag_does_not_leak() -> None:
    out = strip_html('<style media="x onload=evil()>body{color:red}</style><p>visible</p>')
    assert "evil" not in out
    assert "color:red" not in out


def test_stray_close_does_not_leak_other_drop_element_content() -> None:
    # A stray </option> must not zero a counter held by an open <select> and leak its content.
    out = strip_html("<select><option>opt</option>HIDDEN-IN-SELECT</select><p>ok</p>")
    assert out == "ok"
    assert "HIDDEN-IN-SELECT" not in out


def test_svg_and_noscript_and_comment_payloads_dropped() -> None:
    assert strip_html("<svg><script>evil()</script></svg><p>ok</p>") == "ok"
    assert strip_html("<noscript><script>x()</script>NOSCRIPT</noscript><p>ok</p>") == "ok"
    assert strip_html("<!-- <script>evil()</script> hidden --><p>ok</p>") == "ok"
