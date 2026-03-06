import re


def markdown_to_telegram_html(text: str) -> str:
    """Convert Markdown to Telegram HTML (parse_mode=HTML).

    Approach:
    1. Extract code blocks → placeholders (protect from HTML escaping)
    2. Escape HTML entities in remaining plain text (& → &amp;, < → &lt;, > → &gt;)
    3. Convert Markdown syntax to HTML tags (markers like ** are not HTML chars)
    4. Restore code blocks with their own HTML-escaped content

    Supported conversions:
      **bold** / __bold__  → <b>bold</b>
      *italic*             → <i>italic</i>
      _italic_             → <i>italic</i>  (non-word boundary)
      ~~strike~~           → <s>strike</s>
      `code`               → <code>code</code>
      ```block```          → <pre>block</pre>
      # Header             → <b>Header</b>
      [text](url)          → <a href="url">text</a>
    """
    placeholders: list[str] = []

    def _protect(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00CODE{len(placeholders) - 1}\x00"

    # Protect fenced code blocks first, then inline code
    text = re.sub(r"```[\s\S]*?```", _protect, text)
    text = re.sub(r"`[^`]+`", _protect, text)

    # Escape HTML entities in all remaining plain text.
    # Markdown markers (**, *, ~~, _, #, [, ]) are not HTML chars so they survive.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Headers: # Header → <b>Header</b> (up to h6)
    text = re.sub(
        r"^#{1,6}\s+(.+)$",
        r"<b>\1</b>",
        text,
        flags=re.MULTILINE,
    )

    # Links: [text](url) → <a href="url">text</a>
    # Note: & in URL is already &amp; from the escaping step (correct HTML)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Bold: **text** or __text__ → <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)

    # Italic: *text* (single *, after bold consumed above)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)

    # Italic: _text_ — only match when not surrounded by word characters
    # to avoid breaking variable_names_with_underscores
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)

    # Strikethrough: ~~text~~ → <s>text</s>
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # Restore code blocks with HTML-escaped content
    def _restore(m: re.Match) -> str:
        idx = int(m.group(1))
        original = placeholders[idx]
        if original.startswith("```"):
            # Strip ``` fence and optional language tag
            inner = re.sub(r"^```[^\n]*\n?", "", original)
            inner = re.sub(r"```\s*$", "", inner)
            inner = inner.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<pre>{inner}</pre>"
        else:
            # Inline code: strip backticks
            inner = original[1:-1]
            inner = inner.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<code>{inner}</code>"

    text = re.sub(r"\x00CODE(\d+)\x00", _restore, text)

    return text
