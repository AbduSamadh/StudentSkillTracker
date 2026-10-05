import re
from typing import Annotated

from pydantic import AfterValidator

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s.]{2,63}$")


def _email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > 320 or not _EMAIL.match(v):
        raise ValueError("not a valid email address")
    return v


# Syntax-only check for sign-in identifiers. Unlike EmailStr it accepts reserved domains
# (.test, .example) used by synthetic demo and staging accounts.
Email = Annotated[str, AfterValidator(_email)]
