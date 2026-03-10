import re

# Pre-compiled regexes for markdown→WhatsApp conversion (used per message)
_RE_FENCED_CODE = re.compile(r"```[\s\S]*?```")
_RE_INLINE_CODE = re.compile(r"`[^`]+`")
_RE_HEADER = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC = re.compile(r"\*(.+?)\*")
_RE_STRIKE = re.compile(r"~~(.+?)~~")
_RE_CODE_PLACEHOLDER = re.compile(r"\x00CODE(\d+)\x00")

_BOLD_MARK = "\x01BOLD\x01"


def markdown_to_whatsapp(text: str) -> str:
    """Convert Markdown formatting to WhatsApp-compatible formatting.

    Conversions:
    - **bold** → *bold*
    - *italic* or _italic_ → _italic_
    - ~~strike~~ → ~strike~
    - # Header → *Header*
    - [text](url) → text (url)
    - Code blocks preserved as-is
    """
    # Extract code blocks and inline code to protect them
    placeholders: list[str] = []

    def _protect(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00CODE{len(placeholders) - 1}\x00"

    # Protect fenced code blocks first, then inline code
    text = _RE_FENCED_CODE.sub(_protect, text)
    text = _RE_INLINE_CODE.sub(_protect, text)

    # Headers: # Header → *Header* (up to h6) — use bold placeholder
    text = _RE_HEADER.sub(
        lambda m: f"{_BOLD_MARK}{m.group(1)}{_BOLD_MARK}",
        text,
    )

    # Links: [text](url) → text (url)
    text = _RE_LINK.sub(r"\1 (\2)", text)

    # Bold: **text** → use temp placeholder to avoid collision with italic *
    text = _RE_BOLD.sub(lambda m: f"{_BOLD_MARK}{m.group(1)}{_BOLD_MARK}", text)

    # Italic: *text* → _text_  (only single * left after bold conversion)
    text = _RE_ITALIC.sub(r"_\1_", text)

    # Restore bold placeholder → *text*
    text = text.replace(_BOLD_MARK, "*")

    # Strikethrough: ~~text~~ → ~text~
    text = _RE_STRIKE.sub(r"~\1~", text)

    # Restore code blocks
    def _restore(m: re.Match) -> str:
        idx = int(m.group(1))
        return placeholders[idx]

    text = _RE_CODE_PLACEHOLDER.sub(_restore, text)

    return text
