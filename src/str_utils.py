"""String-related utilities."""


def strip_double_quotes(string: str) -> str:
    if string[0] == '"' and string[-1] == '"':
        return string[1:-1]
    return string
