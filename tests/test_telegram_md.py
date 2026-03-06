"""Tests for app.formatting.telegram_md — Markdown → Telegram HTML."""

from app.formatting.telegram_md import markdown_to_telegram_html


def test_bold_double_asterisk():
    assert markdown_to_telegram_html("**hello**") == "<b>hello</b>"


def test_bold_double_underscore():
    assert markdown_to_telegram_html("__hello__") == "<b>hello</b>"


def test_italic_single_asterisk():
    assert markdown_to_telegram_html("*hello*") == "<i>hello</i>"


def test_italic_underscore():
    assert markdown_to_telegram_html("_hello_") == "<i>hello</i>"


def test_italic_underscore_not_inside_word():
    # variable_name should not be italicized
    result = markdown_to_telegram_html("some_variable_name")
    assert "<i>" not in result
    assert "some_variable_name" in result


def test_strikethrough():
    assert markdown_to_telegram_html("~~hello~~") == "<s>hello</s>"


def test_header_h1():
    assert markdown_to_telegram_html("# Title") == "<b>Title</b>"


def test_header_h3():
    assert markdown_to_telegram_html("### Deep Header") == "<b>Deep Header</b>"


def test_link():
    result = markdown_to_telegram_html("[click here](https://example.com)")
    assert result == '<a href="https://example.com">click here</a>'


def test_link_with_ampersand_in_url():
    result = markdown_to_telegram_html("[search](https://example.com?a=1&b=2)")
    # & in URL should be escaped to &amp; (correct HTML)
    assert "href=" in result
    assert "&amp;" in result


def test_inline_code():
    result = markdown_to_telegram_html("Use `print()` function")
    assert "<code>print()</code>" in result
    assert "`" not in result


def test_code_block():
    text = "```python\nprint('hello')\n```"
    result = markdown_to_telegram_html(text)
    assert "<pre>" in result
    assert "</pre>" in result
    assert "print" in result


def test_code_block_html_escaped():
    """Code content must have HTML entities escaped."""
    text = "```\nif x < y:\n    pass\n```"
    result = markdown_to_telegram_html(text)
    assert "&lt;" in result
    assert "<pre>" in result


def test_html_special_chars_escaped_in_plain_text():
    result = markdown_to_telegram_html("5 < 10 & 3 > 2")
    assert "&lt;" in result
    assert "&gt;" in result
    assert "&amp;" in result
    assert "<" not in result.replace("&lt;", "").replace("<pre>", "").replace("<b>", "").replace(
        "<i>", ""
    )


def test_code_not_double_escaped():
    """Code blocks should NOT be double-escaped (& → &amp;&amp;)."""
    text = "`a & b`"
    result = markdown_to_telegram_html(text)
    assert "<code>a &amp; b</code>" == result


def test_bold_and_italic_combined():
    result = markdown_to_telegram_html("**bold** and *italic*")
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result


def test_mixed_formatting():
    text = "# Title\n\n**bold** and *italic* with ~~strike~~\n[link](url)"
    result = markdown_to_telegram_html(text)
    assert "<b>Title</b>" in result
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert "<s>strike</s>" in result
    assert '<a href="url">link</a>' in result


def test_plain_text_unchanged():
    text = "Just plain text with no formatting."
    assert markdown_to_telegram_html(text) == text


def test_code_block_content_not_italicized():
    """Content inside ``` should not be converted as markdown."""
    text = "```\n**not bold** *not italic*\n```"
    result = markdown_to_telegram_html(text)
    assert "<b>" not in result
    assert "<i>" not in result
    assert "<pre>" in result
