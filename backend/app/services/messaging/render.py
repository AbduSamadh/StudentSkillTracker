"""Template rendering with named variables in a sandbox. Missing variables are an error,
never a blank — a half-rendered message is worse than no message."""

import re

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)
_VAR = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


class RenderError(ValueError):
    pass


def variables_in(*texts: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        for m in _VAR.finditer(t or ""):
            seen.setdefault(m.group(1), None)
    return list(seen)


def validate_template(*texts: str) -> None:
    for t in texts:
        try:
            _env.parse(t or "")
        except TemplateSyntaxError as exc:
            raise RenderError(f"Template syntax error: {exc.message}") from exc


def render(text: str, context: dict) -> str:
    try:
        return _env.from_string(text).render(**context).strip()
    except UndefinedError as exc:
        raise RenderError(f"Missing variable: {exc.message}") from exc
