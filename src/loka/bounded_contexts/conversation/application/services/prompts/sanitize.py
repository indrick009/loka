"""Clean a generated sentence before it reaches a user."""

from __future__ import annotations


def collapse_repeats(text: str) -> str:
    """Drop a phrase the model emitted several times in a row.

    A reply model occasionally loops on a short fragment ("en sus en sus en sus
    en plus du loyer"). The loop is a decoding artefact, not meaning, and sending
    it verbatim makes the assistant look broken. Runs of a repeated one- or
    two-word unit collapse to a single occurrence; numeric tokens are left alone
    so a price never loses a digit.
    """
    tokens = text.split()
    out: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        for unit in (2, 1):
            if index + 2 * unit > total:
                continue
            fragment = [token.casefold() for token in tokens[index : index + unit]]
            if not any(any(char.isalpha() for char in token) for token in fragment):
                continue
            end = index + unit
            while (
                end + unit <= total
                and [token.casefold() for token in tokens[end : end + unit]] == fragment
            ):
                end += unit
            if end > index + unit:
                out.extend(tokens[index : index + unit])
                index = end
                break
        else:
            out.append(tokens[index])
            index += 1
    return " ".join(out)
