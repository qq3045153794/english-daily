from __future__ import annotations

import html
from typing import Optional

try:
    import markdown as _markdown
except Exception:
    _markdown = None


SPEAK_JS = """
<script>
document.addEventListener('click', function (event) {
  var el = event.target.closest ? event.target.closest('.speak') : null;
  if (!el) return;
  var word = el.getAttribute('data-word') || '';
  if (!word || !window.speechSynthesis) return;
  var utter = new SpeechSynthesisUtterance(word);
  utter.lang = 'en-US';
  utter.rate = 0.9;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
});
</script>
"""

STYLE = """
:root { color-scheme: light dark; }
body { max-width: 860px; margin: 0 auto; padding: 1.2rem; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif; line-height: 1.65; }
h1, h2, h3 { line-height: 1.3; margin-top: 1.5em; }
a { color: #0969da; }
blockquote { margin-left: 0; padding-left: 1rem; border-left: 4px solid #d0d7de; color: #57606a; }
code { background: rgba(175,184,193,.2); padding: .15em .35em; border-radius: 4px; }
details { margin: .8rem 0; padding: .6rem .8rem; border: 1px solid #d0d7de; border-radius: 6px; }
.calendar { line-height: 2.1; font-size: 1.15em; }
.calendar .day { display: inline-block; min-width: 1.6em; text-align: center; }
.speak { cursor: pointer; font-size: .9em; opacity: .75; }
.speak:hover { opacity: 1; }
footer { margin-top: 3rem; color: #57606a; font-size: .9em; }
"""


def md_to_html(text: str) -> str:
    if _markdown is None:
        # Minimal fallback: paragraphs and line breaks.
        return "<p>" + html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    return _markdown.markdown(text, extensions=["extra", "tables", "sane_lists"])


def strip_front_matter(text: str) -> str:
    text = text or ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return text


def page_html(title: str, markdown_text: str, include_speak: bool = False, home_href: Optional[str] = None) -> str:
    body = md_to_html(strip_front_matter(markdown_text))
    script = SPEAK_JS if include_speak else ""
    home = '<p><a href="%s">← 返回首页</a></p>' % home_href if home_href else ""
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>%s</title>
<style>%s</style>
%s
</head>
<body>
%s
%s
<footer>English Daily · 内容归档在 GitHub，交互在 Telegram。</footer>
</body>
</html>
""" % (html.escape(title), STYLE, script, home, body)
