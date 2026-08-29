"""Detection of marks that are too unclear to grade automatically.

The reader decides whether a bubble is filled by comparing its darkness
against a threshold computed for the page. That works well for marks that are
either clearly filled or clearly blank, but a half-erased answer or a stray
pencil scuff can land between the two, where the verdict is a coin flip. Those
are exactly the marks a human should settle.

Judging "between the two" against the threshold itself does not work, because
the threshold is chosen to sit in the largest gap in the page's own data - if
every mark on a page is light, the threshold simply moves down to suit, and
nothing looks unusual. So instead each bubble is measured against the page's
own two reference points:

* `blank_level` - how dark a typical unmarked bubble is (the printed ring and
  its option letter are not nothing);
* `mark_level` - how dark a typical deliberate mark is.

Rescaled between those, an untouched bubble reads 0.0 and a properly filled
one reads 1.0. Anything landing in the middle band is reported for hand
grading. That measure is immune to a light-handed student, a dark scan, or a
toner-saving printer, because all three move both reference points together.
"""

import typing as tp

# --- tuning --------------------------------------------------------------

#: Half-width of the "cannot call it" band around the midpoint between a blank
#: bubble and a real mark. 0.30 means anything between 20% and 80% as dark as
#: a normal mark on the same page is sent for hand grading.
DEFAULT_ANSWER_MARGIN = 0.30

#: For a digit / single-choice block the reader picks the darkest bubble. That
#: choice is only trusted when the runner-up is clearly lighter: the relative
#: gap ``(darkest - runner_up) / darkest`` must be at least this much.
DEFAULT_ID_CONTRAST = 0.35

#: A block whose darkest bubble is below this is considered unmarked. Matches
#: the noise floor used by ``grid_reading.NumberGridField``.
ID_NOISE_FLOOR = 0.05

#: The blank and mark levels have to be at least this far apart before the
#: rescaled measure means anything. Below it we fall back to comparing against
#: the page threshold directly.
MINIMUM_SEPARATION = 0.05


class AmbiguousMarkError(RuntimeError):
    """Raised when a batch contains marks that need to be graded by hand."""

    def __init__(self, issues: tp.Sequence["MarkIssue"],
                 report_path: tp.Optional[str] = None):
        self.issues = list(issues)
        self.report_path = report_path
        pages = len({issue.source for issue in self.issues})
        message = (
            f"{len(self.issues)} mark(s) on {pages} page(s) are too unclear "
            "to grade automatically and must be checked by hand")
        if report_path:
            message += f". See {report_path}"
        super().__init__(message)


class MarkIssue(tp.NamedTuple):
    """One mark that a human needs to look at."""

    source: str
    """Label of the page the mark is on, e.g. ``batch.pdf (page 3)``."""

    student_id: str
    location: str
    """Where on the page, e.g. ``Q17 (Test ID 1002)`` or ``Student ID digit 2``."""

    kind: str
    """``borderline``, ``multiple``, or ``blank``."""

    detail: str
    """The measurements behind the verdict, for a human to sanity-check."""

    def as_row(self) -> tp.List[str]:
        return [
            self.source, self.student_id, self.location, self.kind, self.detail
        ]


REPORT_HEADER = [
    "Source File", "Student ID", "Location", "Problem", "Measurements"
]

KIND_DESCRIPTIONS = {
    "borderline":
    "too faint or too partly erased to call filled or blank",
    "multiple": "more than one bubble is filled",
    "blank": "no bubble is filled, but a value is required",
}


class PageLevels(tp.NamedTuple):
    """What a blank bubble and a real mark look like on one page."""

    threshold: float
    blank_level: float
    mark_level: float

    @property
    def separation(self) -> float:
        return self.mark_level - self.blank_level

    @property
    def is_usable(self) -> bool:
        return self.separation >= MINIMUM_SEPARATION

    def darkness(self, fill: float) -> float:
        """Rescale a fill percent: 0.0 is an untouched bubble, 1.0 a real mark."""
        if not self.is_usable:
            # No usable reference points (a page with no marks at all, say):
            # fall back to measuring against the threshold, where 1.0 is
            # twice the threshold.
            return fill / (2 * self.threshold) if self.threshold else 0.0
        return (fill - self.blank_level) / self.separation


def _median(values: tp.Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def estimate_levels(fills: tp.Iterable[float], threshold: float) -> PageLevels:
    """Work out a page's blank and mark reference levels.

    Medians are used rather than means so that a handful of odd bubbles - the
    very marks we are trying to find - do not drag the references towards
    themselves.
    """
    marked: tp.List[float] = []
    blank: tp.List[float] = []
    for fill in fills:
        (marked if fill > threshold else blank).append(fill)
    return PageLevels(threshold=float(threshold),
                      blank_level=_median(blank),
                      mark_level=_median(marked) if marked else 0.0)


def _format_fills(fills: tp.Sequence[float], labels: tp.Sequence[str],
                  levels: tp.Optional[PageLevels] = None) -> str:
    ranked = sorted(range(len(fills)), key=lambda i: fills[i], reverse=True)
    parts = []
    for index in ranked[:3]:
        label = labels[index] if index < len(labels) else str(index)
        if levels is not None:
            parts.append(f"{label}={levels.darkness(fills[index]) * 100:.0f}%")
        else:
            parts.append(f"{label}={fills[index] * 100:.0f}% fill")
    return " ".join(parts)


def review_answer(fills: tp.Sequence[float], levels: PageLevels,
                  labels: tp.Sequence[str],
                  margin: float = DEFAULT_ANSWER_MARGIN
                  ) -> tp.Optional[tp.Tuple[str, str]]:
    """Check one multiple-choice question.

    Returns ``(kind, detail)`` if the question needs a human, else ``None``.
    Darkness is given as a percentage of a normal mark on the same page, so
    30% means "about a third as dark as this student's other answers".
    """
    if not fills:
        return None
    low = 0.5 - margin
    high = 0.5 + margin
    darkness = [levels.darkness(fill) for fill in fills]

    unclear = [i for i, value in enumerate(darkness) if low <= value <= high]
    if unclear:
        names = ", ".join(
            labels[i] if i < len(labels) else str(i) for i in unclear)
        return ("borderline",
                f"{names} only partly filled; {_format_fills(fills, labels, levels)} "
                f"of a normal mark")

    filled = [i for i, value in enumerate(darkness) if value > high]
    if len(filled) > 1:
        names = ", ".join(
            labels[i] if i < len(labels) else str(i) for i in filled)
        return ("multiple",
                f"{names} all filled; {_format_fills(fills, labels, levels)} "
                f"of a normal mark")
    return None


def review_choice_block(fills: tp.Sequence[float],
                        labels: tp.Sequence[str],
                        required: bool = True,
                        min_contrast: float = DEFAULT_ID_CONTRAST,
                        noise_floor: float = ID_NOISE_FLOOR
                        ) -> tp.Optional[tp.Tuple[str, str]]:
    """Check one digit column, Latin level block, or page code block.

    Unlike a question, these are read by taking the darkest bubble, so what
    decides whether the reading is trustworthy is not how dark that bubble is
    but how far it stands clear of the next darkest. A light-handed student
    still gets the right digit; a half-erased one does not, and shows up here
    as poor contrast.

    They are deliberately *not* measured against the page's mark level the way
    answers are. These bubbles sit in differently shaped grid cells from the
    answer bubbles, so their fill percents are not on the same scale, and
    comparing the two produces false alarms on real scans.
    """
    if not fills:
        return None
    ranked = sorted(fills, reverse=True)
    darkest = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else 0.0

    if darkest <= noise_floor:
        if required:
            return ("blank",
                    f"nothing filled; darkest bubble only "
                    f"{darkest * 100:.0f}% fill")
        return None

    contrast = (darkest - runner_up) / darkest if darkest else 0.0
    if contrast < min_contrast:
        return ("multiple" if runner_up > noise_floor else "borderline",
                f"darkest bubble stands only {contrast * 100:.0f}% clear of "
                f"the next; {_format_fills(fills, labels)}")
    return None


class ReviewCollector:
    """Accumulates the marks that need a human across a whole batch."""

    def __init__(self,
                 answer_margin: float = DEFAULT_ANSWER_MARGIN,
                 id_contrast: float = DEFAULT_ID_CONTRAST,
                 enabled: bool = True):
        self.answer_margin = answer_margin
        self.id_contrast = id_contrast
        self.enabled = enabled
        self.issues: tp.List[MarkIssue] = []

    def add(self, source: str, student_id: str, location: str,
            verdict: tp.Optional[tp.Tuple[str, str]]):
        if verdict is None or not self.enabled:
            return
        kind, detail = verdict
        self.issues.append(
            MarkIssue(source=source,
                      student_id=student_id,
                      location=location,
                      kind=kind,
                      detail=detail))

    def check_choice(self, source: str, student_id: str, location: str,
                     fills: tp.Sequence[float], labels: tp.Sequence[str],
                     required: bool = True):
        if not self.enabled:
            return
        self.add(
            source, student_id, location,
            review_choice_block(fills, labels, required=required,
                                min_contrast=self.id_contrast))

    @property
    def any_issues(self) -> bool:
        return bool(self.issues)

    def rows(self) -> tp.List[tp.List[str]]:
        return [REPORT_HEADER] + [issue.as_row() for issue in self.issues]

    def summary(self, limit: int = 10) -> str:
        if not self.issues:
            return "No unclear marks found."
        lines = [f"{len(self.issues)} mark(s) need to be checked by hand:"]
        for issue in self.issues[:limit]:
            lines.append(f"  {issue.source} | {issue.location} | "
                         f"{KIND_DESCRIPTIONS.get(issue.kind, issue.kind)} | "
                         f"{issue.detail}")
        if len(self.issues) > limit:
            lines.append(f"  ... and {len(self.issues) - limit} more")
        return "\n".join(lines)
