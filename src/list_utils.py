"""List processing and handling utilities."""

import math
import typing as tp

T = tp.TypeVar('T')


def find_greatest_value_indexes(values: tp.List[float],
                                n: int) -> tp.List[int]:
    """Find the indices of the greatest `n` numbers in `items`.

    Returns:
        A list where the first element is the index of the greatest item and the
        second element is the index of the second-greatest item and so on.
    """
    items_copy = values.copy()
    indexes = []
    for _ in range(n):
        max_index = find_max_value_index(items_copy)
        indexes.append(max_index)
        items_copy[max_index] = -math.inf
    return indexes


def find_max_value_index(values: tp.List[float]) -> int:
    """Returns the index of the greatest value in `items`."""
    max_value = -math.inf
    max_index = -1
    for i, value in enumerate(values):
        if value > max_value:
            max_value = value
            max_index = i
    return max_index


def is_adjacent_indexes(items: tp.List[tp.Any], index_a: int, index_b: int):
    """Check that the given indices are next to each other or are the first and
  last indices in the list. Order of a and b do not matter."""
    return next_index(items, index_a) == index_b or prev_index(
        items, index_a) == index_b


def call_on_some(items: tp.List[tp.Any], indexes: tp.List[int],
                 fn: tp.Callable[[tp.Any], tp.Any]) -> list:
    """Return a copy of the list with the results of `fn` called on the items in
    `items` with the indexes in `indexes`. Other items remain the same."""
    return [el if i not in indexes else fn(el) for i, el in enumerate(items)]


def next_index(items: tp.List[tp.Any], index: int) -> int:
    """Return the next index up in the list, looping back to the beginning if needed."""
    return index + 1 if index + 1 < len(items) else 0


def prev_index(items: tp.List[tp.Any], index: int) -> int:
    """Return the previous index up in the list, looping back to the end if needed."""
    return index - 1 if index - 1 >= 0 else len(items) - 1


Pair = tp.Tuple[int, int]


def arrange_index_to_first(items: tp.List[tp.Any], index: int) -> list:
    if index >= len(items) or index < 0:
        raise IndexError("Index is invalid.")

    result = [items[index]]
    i = next_index(items, index)
    while i != index:
        result.append(items[i])
        i = next_index(items, i)
    return result


def determine_which_is_next(items: tp.List[tp.Any], index_a: int,
                            index_b: int) -> int:
    if next_index(items, index_a) == index_b:
        return index_b
    return index_a
