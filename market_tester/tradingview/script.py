"""Pine-style order commands for numba scripts (`strategy.entry`, `strategy.exit`, `strategy.cancel`,
`strategy.close_all`). A script is an njit function called at every bar close:

    script(k, o, h, l, c, minute, day, feat_i, feat_f, params, state, book, pos) -> None

It reads bar k and earlier, its own int `state`, and issues orders into `book` / `pos` with the helpers below.
Orders work from bar k+1 (`close_all(immediately)` fills at bar k's close). Semantics: SPEC.md.
"""

import numba as nb
import numpy as np

# order book: one row per order slot
NORD = 8
ACTIVE, ROLE, TYPE, DIR, PRICE, OCA, DAY, LASTMIN, TP, SL, TAG, ACTIVATED, SUBMIT = range(13)
NFIELD = 13  # ACTIVATED: a stop triggered at submission, now a market order (SPEC 2b); SUBMIT: bar it was sent
NO_PRICE = -(1 << 62)  # "no price": CL traded below zero (2020-04-20), so 0 is a valid price
ENTRY, EXIT = 0, 1
STOP, LIMIT, MARKET = 1, 2, 3

# position / control
P_DIR, P_PX, P_BAR, P_SEG, P_DAY, P_TAG, P_MIN, P_MAX, P_ETYPE, P_CLOSE_NOW, P_LAST_EXIT_BAR, P_SUBMIT = range(12)
NPOS = 12
NO_LIMIT = -1


def new_book() -> np.ndarray:
    return np.zeros((NORD, NFIELD), np.int64)


def new_pos() -> np.ndarray:
    p = np.zeros(NPOS, np.int64)
    p[P_LAST_EXIT_BAR] = -1
    return p


@nb.njit(cache=True)
def _free_slot(book):
    for s in range(book.shape[0]):
        if book[s, ACTIVE] == 0:
            return s
    raise ValueError("order book full")


@nb.njit(cache=True)
def entry(book, direction, otype, price, oca=0, day=-1, last_minute=NO_LIMIT, tp_ticks=0, sl_price=NO_PRICE, tag=0):
    """strategy.entry: `direction` +1 buy / -1 sell, `otype` STOP / LIMIT / MARKET (price ignored for MARKET).
    `oca` > 0: entries of one group cancel each other on the first fill (oca.cancel).
    `day`, `last_minute`: not worked on bars of another trading day or after that ET minute (NO_LIMIT = none).
    `tp_ticks` / `sl_price`: the pre-submitted strategy.exit (profit from the actual fill, 0 = none / absolute
    stop, NO_PRICE = none)."""
    s = _free_slot(book)
    book[s, ACTIVE] = 1
    book[s, ROLE] = ENTRY
    book[s, TYPE] = otype
    book[s, DIR] = direction
    book[s, PRICE] = price
    book[s, OCA] = oca
    book[s, DAY] = day
    book[s, LASTMIN] = last_minute
    book[s, TP] = tp_ticks
    book[s, SL] = sl_price
    book[s, TAG] = tag
    book[s, ACTIVATED] = 0
    book[s, SUBMIT] = -1  # stamped by the broker after the script returns
    return s


@nb.njit(cache=True)
def _set_exit(book, pos, otype, price):
    for s in range(book.shape[0]):  # one bracket per position: amend the leg (SPEC 5)
        if book[s, ACTIVE] == 1 and book[s, ROLE] == EXIT and (book[s, TYPE] == otype or (
                otype == STOP and book[s, ACTIVATED] == 1)):
            book[s, TYPE] = otype
            book[s, PRICE] = price
            book[s, ACTIVATED] = 0
            return s
    s = _free_slot(book)
    book[s, ACTIVE] = 1
    book[s, ROLE] = EXIT
    book[s, TYPE] = otype
    book[s, DIR] = -pos[P_DIR]
    book[s, PRICE] = price
    book[s, DAY] = -1
    book[s, LASTMIN] = NO_LIMIT
    book[s, OCA] = 0
    book[s, ACTIVATED] = 0
    book[s, SUBMIT] = -1
    return s


@nb.njit(cache=True)
def exit_stop(book, pos, price):
    """strategy.exit(stop=price) for the open position: adds or moves its stop leg."""
    if pos[P_DIR] == 0:
        return -1
    return _set_exit(book, pos, STOP, price)


@nb.njit(cache=True)
def exit_limit(book, pos, price):
    """strategy.exit(limit=price) for the open position: adds or moves its target leg."""
    if pos[P_DIR] == 0:
        return -1
    return _set_exit(book, pos, LIMIT, price)


@nb.njit(cache=True)
def cancel_entries(book):
    """strategy.cancel_all for pending entries."""
    for s in range(book.shape[0]):
        if book[s, ROLE] == ENTRY:
            book[s, ACTIVE] = 0


@nb.njit(cache=True)
def close_all(pos, immediately):
    """strategy.close_all: immediately=True fills at this bar's close, else at the next bar's open."""
    pos[P_CLOSE_NOW] = 1 if immediately else 2


@nb.njit(cache=True)
def has_entries(book):
    for s in range(book.shape[0]):
        if book[s, ACTIVE] == 1 and book[s, ROLE] == ENTRY:
            return True
    return False
