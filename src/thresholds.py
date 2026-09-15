"""Deciding how dark a bubble has to be before it counts as filled.

A bubble is filled if its darkness is above a cutoff. Nothing cleverer: a
student may legitimately mean "A and B", so the reader must not assume one
answer per question and pick the darkest.

The cutoff cannot be a fixed number. A dark scanner, a light pencil, or a
toner-saving printer all move every reading at once, so the cutoff has to be
derived from the scans themselves. That is Otsu's method - but applied to the
list of *bubble darknesses*, not to the image's pixels.

Two wrinkles, both handled below:

*Class imbalance.* Only a few percent of bubbles are filled. Textbook Otsu
maximises ``w0*w1*(m0-m1)^2``; the ``w0*w1`` factor is largest at an even
split, so with a 95/5 population it drags the cutoff down into the blank
cluster and starts calling smudges answers. This module maximises a
Fisher-style ratio instead, ``(m1-m0)^2 / (v0+v1)``, which has no class-size
weighting and so is indifferent to how lopsided the split is.

*Two populations of bubble.* Answer bubbles and metadata bubbles (the ID
digits, the Latin level, the page code) are calibrated separately. On the
CAJCL sheet they come out close together, because every bubble is drawn in an
identically shaped grid cell; on the legacy sheets, whose ID bubbles sit in
differently shaped cells, a filled ID bubble measures about half what a filled
answer bubble does. Keeping them apart costs nothing and means the cutoffs
stay right if the sheet is ever redrawn.
"""

import pathlib
import typing as tp

#: Where the review threshold sits, as a fraction of the way from a typical
#: blank bubble to a typical filled one. 0.12 means anything at least an
#: eighth of the way towards being a real mark, but not dark enough to be
#: selected, is sent for a human to settle. Measured from the *class means*
#: rather than from the cutoff, so a single half-erased bubble cannot move the
#: band away from itself.
#:
#: Deliberately low. A faint mark that falls under it is not reported at all -
#: the student's answer is silently dropped - which is a far worse failure
#: than one more row on a review sheet. The per-test rescue below is what
#: stops that generosity turning into an unmanageable pile of them.
DEFAULT_REVIEW_FRACTION = 0.12

#: When a spec gives only the cutoffs, the review threshold is put here,
#: as a fraction of the cutoff.
SHORT_SPEC_REVIEW_RATIO = 0.5

#: A split is only believed if each side holds at least this fraction of the
#: sample. Stops a single outlier from being declared "the filled class".
MINIMUM_CLASS_FRACTION = 0.002

#: ...and if the two class means are at least this far apart. Below it the
#: sample is all one thing (a blank page, say) and there is nothing to split.
MINIMUM_CLASS_SEPARATION = 0.05

#: Order of the four numbers in a --threshold spec.
SPEC_FIELDS = ("answer_select", "answer_review", "metadata_select",
               "metadata_review")


class ThresholdError(ValueError):
    """Raised when a --threshold spec cannot be understood."""


class Thresholds(tp.NamedTuple):
    """The four numbers that decide how every bubble on a sheet is read.

    All four are *darkness* on a 0-1 scale: 0 is untouched white paper and 1
    is solid black. So a **higher** number demands a **darker** mark before it
    counts, and raising a cutoff makes the reader stricter - fewer bubbles are
    taken as filled. Lowering it makes the reader more willing, at the risk of
    reading a smudge as an answer.
    """

    answer_select: float
    """A question's bubble counts as filled above this darkness."""

    answer_review: float
    """Below `answer_select` but at or above this, a question's bubble is too
    close to call and goes to the review sheet."""

    metadata_select: float
    """An ID / Latin level / page-code bubble counts as filled above this."""

    metadata_review: float
    """The same lower edge, for a metadata bubble."""

    def select_for(self, is_answer: bool) -> float:
        return self.answer_select if is_answer else self.metadata_select

    def review_for(self, is_answer: bool) -> float:
        return self.answer_review if is_answer else self.metadata_review

    def to_spec(self) -> str:
        return ",".join(f"{value:.4f}".rstrip("0").rstrip(".")
                        for value in self)

    def describe(self) -> str:
        width = max(len(name) for name in SPEC_FIELDS)
        lines = [
            f"  {name.replace('_', ' '):<{width}}  {value:.4f}"
            for name, value in zip(SPEC_FIELDS, self)
        ]
        return "\n".join(lines)


#: Names accepted in a --threshold spec, and the field each sets.
SPEC_ALIASES = {
    "answer": "answer_select",
    "answer-select": "answer_select",
    "answer-review": "answer_review",
    "metadata": "metadata_select",
    "metadata-select": "metadata_select",
    "metadata-review": "metadata_review",
}


class PartialThresholds(tp.NamedTuple):
    """A --threshold spec, which need not set everything.

    Whatever is left out is calibrated from the scans as usual, so
    ``--threshold answer=0.42`` pins the answer cutoff and lets the rest be
    measured.
    """

    values: tp.Dict[str, float]

    def apply_to(self, calibrated: Thresholds) -> Thresholds:
        return calibrated._replace(**self.values)

    @property
    def is_complete(self) -> bool:
        return len(self.values) == len(SPEC_FIELDS)

    @property
    def names(self) -> tp.List[str]:
        return [name for name in SPEC_FIELDS if name in self.values]


def parse_spec(spec: str) -> PartialThresholds:
    """Read a --threshold value.

    Accepts, in any mixture:

    * all four numbers in order, ``0.42,0.19,0.36,0.20``;
    * two numbers, the cutoffs alone, ``0.42,0.36``;
    * one number, used as both cutoffs;
    * blanks to leave a slot to calibration, ``0.42,0.19,,``;
    * names, ``answer=0.42`` or ``metadata-review=0.2``, alone or mixed in.
    """
    raw = [part.strip() for part in spec.replace(";", ",").split(",")]
    named: tp.Dict[str, float] = {}
    positional: tp.List[tp.Optional[float]] = []

    for part in raw:
        if not part:
            positional.append(None)
            continue
        if "=" in part:
            label, _, value = part.partition("=")
            key = label.strip().lower().replace("_", "-")
            if key not in SPEC_ALIASES:
                raise ThresholdError(
                    f"'{label.strip()}' is not a threshold name. Use one of: "
                    + ", ".join(sorted(SPEC_ALIASES)) + ".")
            try:
                named[SPEC_ALIASES[key]] = float(value)
            except ValueError:
                raise ThresholdError(
                    f"'{part}' does not end in a number.")
            continue
        try:
            positional.append(float(part))
        except ValueError:
            raise ThresholdError(
                f"'{part}' is not a number. Expected something like "
                "'0.42,0.19,0.36,0.20', or 'answer=0.42'.")

    given = [value for value in positional if value is not None]
    values: tp.Dict[str, float] = {}
    if len(positional) == 1 and given:
        values["answer_select"] = given[0]
        values["metadata_select"] = given[0]
    elif len(positional) == 2 and len(given) == 2:
        values["answer_select"], values["metadata_select"] = given
    elif positional:
        if len(positional) > len(SPEC_FIELDS):
            raise ThresholdError(
                f"'{spec}' has {len(positional)} values but there are only "
                f"{len(SPEC_FIELDS)} thresholds: "
                + ", ".join(SPEC_FIELDS) + ".")
        for name, value in zip(SPEC_FIELDS, positional):
            if value is not None:
                values[name] = value
    values.update(named)

    if not values:
        raise ThresholdError(
            f"'{spec}' sets no thresholds. Give a number, or a name such as "
            "'answer=0.42'.")
    for name, value in values.items():
        if not 0.0 < value < 1.0:
            raise ThresholdError(
                f"{name.replace('_', ' ')} is {value:g}, outside 0-1. Bubble "
                "darkness is a fraction of solid black, so every threshold "
                "must be between 0 and 1.")
    for kind in ("answer", "metadata"):
        select = values.get(f"{kind}_select")
        review = values.get(f"{kind}_review")
        if select is not None and review is not None and review >= select:
            raise ThresholdError(
                f"The {kind} review threshold ({review:g}) must be below the "
                f"{kind} cutoff ({select:g}). Anything between the two is "
                "sent for review; above the cutoff is taken as filled.")
    return PartialThresholds(values)


# --- calibration ---------------------------------------------------------


class Split(tp.NamedTuple):
    """Where a population of bubble darknesses divides into blank and filled."""

    cutoff: float
    blank_mean: float
    filled_mean: float
    filled_count: int
    total: int

    @property
    def separation(self) -> float:
        return self.filled_mean - self.blank_mean


def find_split(values: tp.Sequence[float]) -> tp.Optional[Split]:
    """Split one population of bubble darknesses into blank and filled.

    Otsu's idea - try every cutoff, keep the one that separates best - but
    scored with a Fisher ratio rather than Otsu's class-size-weighted
    variance, so that a population of 95% blanks does not drag the cutoff
    down into the blank cluster.

    Returns None when the sample does not actually contain two groups.
    """
    ordered = sorted(float(value) for value in values)
    count = len(ordered)
    if count < 8:
        return None

    minimum_class = max(1, int(count * MINIMUM_CLASS_FRACTION))

    # Running sums let every candidate split be scored in constant time.
    prefix_sum = [0.0] * (count + 1)
    prefix_square = [0.0] * (count + 1)
    for index, value in enumerate(ordered):
        prefix_sum[index + 1] = prefix_sum[index] + value
        prefix_square[index + 1] = prefix_square[index] + (value * value)

    def moments(start: int, stop: int) -> tp.Tuple[float, float]:
        size = stop - start
        total = prefix_sum[stop] - prefix_sum[start]
        square = prefix_square[stop] - prefix_square[start]
        mean = total / size
        variance = max((square / size) - (mean * mean), 0.0)
        return mean, variance

    best_score = 0.0
    best_index: tp.Optional[int] = None
    for boundary in range(minimum_class, count - minimum_class + 1):
        if ordered[boundary - 1] == ordered[boundary]:
            continue  # a split has to fall between two different values
        blank_mean, blank_variance = moments(0, boundary)
        filled_mean, filled_variance = moments(boundary, count)
        spread = blank_variance + filled_variance
        gap = filled_mean - blank_mean
        score = (gap * gap) / (spread + 1e-12)
        if score > best_score:
            best_score = score
            best_index = boundary

    if best_index is None:
        return None

    blank_mean, _ = moments(0, best_index)
    filled_mean, _ = moments(best_index, count)
    if filled_mean - blank_mean < MINIMUM_CLASS_SEPARATION:
        return None

    # Put the cutoff between the darkest blank and the lightest filled bubble
    # rather than on top of either.
    cutoff = (ordered[best_index - 1] + ordered[best_index]) / 2
    return Split(cutoff=cutoff,
                 blank_mean=blank_mean,
                 filled_mean=filled_mean,
                 filled_count=count - best_index,
                 total=count)


def calibrate(answer_fills: tp.Sequence[float],
              metadata_fills: tp.Sequence[float],
              review_fraction: float = DEFAULT_REVIEW_FRACTION,
              fallback: tp.Optional[Thresholds] = None
              ) -> tp.Tuple[Thresholds, tp.List[str]]:
    """Work out both cutoffs from sample pages.

    Returns the thresholds and a list of human-readable notes about anything
    that had to be guessed.
    """
    notes: tp.List[str] = []
    answer_split = find_split(answer_fills)
    metadata_split = find_split(metadata_fills)

    def resolve(split: tp.Optional[Split], kind: str,
                default_select: float) -> tp.Tuple[float, float]:
        if split is None:
            notes.append(
                f"{kind} bubbles: no clear split between blank and filled in "
                f"the sample, so the cutoff fell back to {default_select:.4f}."
                " Check the scans, or set --threshold by hand.")
            return default_select, default_select * SHORT_SPEC_REVIEW_RATIO
        # The review threshold is measured up from the blank cluster, not
        # down from the cutoff: the cutoff itself sits next to whatever the
        # darkest unselected bubble happens to be, which is exactly the mark
        # we are trying to catch.
        review = split.blank_mean + (split.separation * review_fraction)
        return split.cutoff, min(review, split.cutoff * 0.99)

    default = fallback or Thresholds(0.30, 0.05, 0.20, 0.03)
    answer_select, answer_review = resolve(answer_split, "Answer",
                                           default.answer_select)
    metadata_select, metadata_review = resolve(metadata_split, "Metadata",
                                               default.metadata_select)
    return Thresholds(answer_select, answer_review, metadata_select,
                      metadata_review), notes


# --- rescuing a faintly-marked test ---------------------------------------

#: A test is re-read on its own terms once this fraction of its questions are
#: too close to call. Below it the batch cutoff is doing its job and a second
#: opinion would only add noise.
RESCUE_UNCLEAR_FRACTION = 0.35

#: ...and never for a handful of questions, where "most of them" means very
#: little and one odd sheet could trigger it.
RESCUE_MINIMUM_QUESTIONS = 10

#: The rescue has to leave materially fewer questions in doubt than the batch
#: cutoff did, or it is not an improvement and is not worth the divergence.
RESCUE_IMPROVEMENT = 0.5


def unclear_count(groups, thresholds: Thresholds) -> int:
    """How many of these bubble groups are too close to call."""
    return sum(1 for group in groups if group.unclear(thresholds))


def rescue(fills: tp.Sequence[float], thresholds: Thresholds,
           review_fraction: float = DEFAULT_REVIEW_FRACTION
           ) -> tp.Optional[Thresholds]:
    """A cutoff drawn from one test's own bubbles, if they split cleanly.

    Returns None when there is no convincing split - which is the common case,
    and the point: a test is only read on its own terms when its own marks say
    where the line is, never merely because the batch cutoff was inconvenient.
    """
    split = find_split(fills)
    if split is None:
        return None
    # A cutoff above the batch's own would make the reader stricter, which is
    # not what this is for and risks dropping marks that were being read
    # perfectly well.
    if split.cutoff >= thresholds.answer_select:
        return None
    review = split.blank_mean + (split.separation * review_fraction)
    return thresholds._replace(
        answer_select=split.cutoff,
        answer_review=min(review, split.cutoff * 0.99))


# --- reading a question by contrast ---------------------------------------

#: How far above the known-unfilled reference a bubble must sit before it can
#: be a deliberate mark at all. The floor this produces has to clear the
#: darkest blank on the page without reaching the faintest real answer.
CONTRAST_MARGIN = 1.6

#: ...and how many times the rest of its own row it must be. This is what
#: separates a light pencil mark from a page that is simply grubby: a real
#: mark towers over its neighbours, whereas dirt raises the whole row.
CONTRAST_RATIO = 4.0

#: Anything at least this fraction of the darkest bubble in the row counts as
#: marked too, so a deliberate "A and B" is not reduced to whichever of the
#: two the student pressed harder.
CONTRAST_SPREAD = 0.55

#: Spread of the known-unfilled reference, in median absolute deviations. Six
#: is far out on any sane distribution, which is what is wanted: the reference
#: has to be an answer to "how dark does a blank bubble get", not "how dark is
#: a typical one".
CONTRAST_REFERENCE_DEVIATIONS = 6

#: Below this many reference bubbles there is not enough to be robust about.
CONTRAST_MINIMUM_REFERENCE = 20


def blank_reference(digit_groups: tp.Sequence[tp.Sequence]
                    ) -> tp.Optional[float]:
    """How dark a bubble gets on this page when nobody has filled it in.

    Takes every bubble of every digit block except the darkest in each - those
    are blank by construction, since a digit block holds one answer and nine
    blanks - and returns a robust upper edge of that population.
    """
    blanks: tp.List[float] = []
    for group in digit_groups:
        fills = sorted(group.fills, reverse=True)
        blanks.extend(fills[1:])
    if len(blanks) < CONTRAST_MINIMUM_REFERENCE:
        return None
    ordered = sorted(blanks)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else \
        (ordered[middle - 1] + ordered[middle]) / 2
    deviations = sorted(abs(value - median) for value in ordered)
    spread = deviations[len(deviations) // 2]
    return median + CONTRAST_REFERENCE_DEVIATIONS * spread


def read_by_contrast(group, reference: float
                     ) -> tp.Optional[tp.Set[str]]:
    """What this question says, judged against its own row.

    Returns the letters marked, an empty set for a question left blank, or
    None when the row is genuinely ambiguous and still needs a person.
    """
    floor = reference * CONTRAST_MARGIN
    ranked = sorted(zip(group.fills, group.labels), reverse=True)
    if not ranked:
        return None
    darkest = ranked[0][0]
    if darkest < floor:
        return set()            # nothing here; the student skipped it

    rest = sorted(value for value, _ in ranked[1:])
    if not rest:
        return None
    middle = rest[len(rest) // 2]
    # Compared against the middle of the row rather than the second darkest,
    # so a legitimate two-letter answer does not look like a failure to stand
    # out. The spread rule below is what picks the second letter back up.
    if middle > 0 and darkest < middle * CONTRAST_RATIO:
        return None
    if middle <= 0 and darkest < floor:
        return None

    return {label for value, label in ranked
            if value >= darkest * CONTRAST_SPREAD and value >= floor}


# --- the calibration report ----------------------------------------------


CALIBRATION_FILENAME = "Calibration.txt"


def write_report(path: pathlib.Path,
                 per_file: tp.Sequence[tp.Tuple[str, Thresholds]],
                 notes: tp.Sequence[str] = (),
                 supplied: bool = False,
                 batch: tp.Optional[str] = None,
                 pinned: tp.Sequence[str] = ()) -> pathlib.Path:
    """Write Calibration.txt: what was used, and how to reuse it."""
    lines: tp.List[str] = []
    if batch:
        lines.append(f"Batch {batch}")
        lines.append("")
    lines.append("Every number below is bubble darkness, from 0 (untouched")
    lines.append("white paper) to 1 (solid black). A HIGHER number is")
    lines.append("STRICTER: it demands a darker mark before the bubble")
    lines.append("counts as filled.")
    lines.append("")
    if pinned:
        lines.append("Set on the command line: " + ", ".join(
            name.replace("_", " ") for name in pinned) + ".")
        lines.append("The rest were calibrated from the scans.")
        lines.append("")
    if supplied:
        lines.append("Thresholds were supplied on the command line.")
    else:
        lines.append("Thresholds were calibrated from the scans themselves.")
        lines.append("")
        lines.append("A bubble counts as filled when its darkness is above")
        lines.append("the cutoff. Anything between the review threshold and")
        lines.append("the cutoff is too close to call, so it is left alone")
        lines.append("and sent to the review sheet instead.")
    lines.append("")

    for name, thresholds in per_file:
        lines.append(f"{name}")
        lines.append(thresholds.describe())
        lines.append(f"  --threshold {thresholds.to_spec()}")
        lines.append("")

    if notes:
        lines.append("Notes")
        lines.extend(f"  {note}" for note in notes)
        lines.append("")

    if not supplied and len(per_file) == 1:
        lines.append("To grade again with exactly these numbers, add:")
        lines.append(f"  --threshold {per_file[0][1].to_spec()}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
