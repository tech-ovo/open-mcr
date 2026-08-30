"""What the operator sees while a batch runs.

Deliberately quiet. Per-sheet trouble - an unreadable mark, an unknown Test ID
- belongs in the review sheets and the results, not scrolling past on the
terminal. Only a *breaking* problem, one that means no output should be
produced at all, interrupts.
"""

import shutil
import sys
import typing as tp

#: Width of the drawn bar, not counting the label or the counter.
BAR_WIDTH = 24
#: Block characters where the console can render them, plain ASCII where it
#: cannot - a Windows console on a legacy code page turns them into "?".
BLOCKS = ("█", "─")
ASCII_BLOCKS = ("#", "-")


def plural(count: int, singular: str, plural_form: tp.Optional[str] = None
           ) -> str:
    """'1 file' / '2 files'."""
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count} {word}"


def verb(count: int, singular: str, plural_form: str) -> str:
    """'1 mark needs' / '2 marks need'."""
    return singular if count == 1 else plural_form


def quoted_list(names: tp.Sequence[str]) -> str:
    return " ".join(f"'{name}'" for name in names)


class Console:
    """Writes progress to a stream, quietly."""

    def __init__(self, stream: tp.Optional[tp.TextIO] = None,
                 enabled: bool = True):
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = enabled
        self._bar_open = False
        # An animated bar only makes sense on a terminal. Redirected to a file
        # or a pipe, each bar is written once when it finishes.
        self.animate = bool(getattr(self.stream, "isatty", lambda: False)())
        self.filled_block, self.empty_block = (
            BLOCKS if self._can_encode(BLOCKS[0] + BLOCKS[1])
            else ASCII_BLOCKS)

    def _can_encode(self, text: str) -> bool:
        encoding = getattr(self.stream, "encoding", None)
        if not encoding:
            return False
        try:
            text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            return False
        return True

    # --- plain lines ---

    def line(self, text: str = "", indent: int = 0):
        if not self.enabled:
            return
        self._close_bar()
        self.stream.write(("  " * indent) + text + "\n")
        self.stream.flush()

    def error(self, text: str):
        self._close_bar()
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    # --- progress bars ---

    def progress(self, label: str, total: int, indent: int = 1
                 ) -> "ProgressBar":
        return ProgressBar(self, label, total, indent)

    def _write_bar(self, text: str, final: bool = False):
        if not self.enabled:
            return
        if not self.animate:
            # Redirected to a file or a pipe, where a carriage return would
            # just pile the frames up: say it once, when it is finished.
            if final:
                self.stream.write(text + "\n")
                self.stream.flush()
            return
        width = shutil.get_terminal_size((100, 24)).columns
        self.stream.write("\r" + text[:max(width - 1, 10)])
        self.stream.flush()
        self._bar_open = True

    def _close_bar(self):
        if self._bar_open and self.enabled:
            self.stream.write("\n")
            self.stream.flush()
            self._bar_open = False


class ProgressBar:
    """A one-line bar that counts pages."""

    def __init__(self, console: Console, label: str, total: int, indent: int):
        self.console = console
        self.label = label
        self.total = max(total, 1)
        self.indent = indent
        self.done = 0
        self._render()

    def _render(self, final: bool = False):
        filled = int(BAR_WIDTH * self.done / self.total)
        bar = ((self.console.filled_block * filled) +
               (self.console.empty_block * (BAR_WIDTH - filled)))
        # Bar first, then a fixed-width counter, then the label. Labels vary
        # in length ("Processing 'a.pdf'." against "Annotating 'batch.pdf'."),
        # so putting them last is the only way the bars line up.
        width = len(str(self.total))
        counter = f"{self.done:>{width}}/{self.total}"
        prefix = "  " * self.indent
        self.console._write_bar(
            f"{prefix}{bar} {counter}  {self.label}", final=final)

    def step(self, amount: int = 1):
        self.done = min(self.done + amount, self.total)
        self._render()

    def finish(self):
        self.done = self.total
        self._render(final=True)
        self.console._close_bar()

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *_):
        self.finish()
        return False
