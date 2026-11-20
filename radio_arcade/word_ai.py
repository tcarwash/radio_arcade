"""Bounded, open-information practice opponent for Open Word Tiles.

Searches the bundled ENABLE1 dictionary and validates prospective plays with
the game's exact rules and scoring. No hidden data or online lookup required.
"""
from collections import Counter
from functools import lru_cache
import random
import time

from .games.scrabble import _lexicon, _board_letter, TILE_POINTS, EMPTY_RACK, BLANK, SIZE, make_placement


@lru_cache(maxsize=1)
def _words():
    # We intentionally limit search to rack-sized words plus one board anchor,
    # not claim a full crossword solver.
    return sorted((w for w in _lexicon() if 2 <= len(w) <= 8),
                  key=lambda w:(sum(TILE_POINTS[ord(ch)-64] for ch in w),len(w)), reverse=True)


def _word_with_blanks(word, first, step, board, stock):
    letters = Counter(stock)
    chunks = []
    for index, char in enumerate(word):
        pos = first + step * index
        fixed = board[pos]
        if fixed:
            if _board_letter(fixed) != char:
                return None
            chunks.append(char)
            continue
        if letters[char]:
            letters[char] -= 1
            chunks.append(char)
        elif letters["?"]:
            letters["?"] -= 1
            chunks.append("{" + char + "}")
        else:
            return None
    return "".join(chunks)


def choose_word(game, state, players, actor, *, difficulty="standard", rng=random):
    rack=state["racks"][players.index(actor)]
    letters=[chr(64+t) if t else "?" for t in rack if t!=EMPTY_RACK]
    if not letters:
        return b"\x01"
    stock=Counter(letters)
    board=state["board"]
    anchors=[(pos,_board_letter(value)) for pos,value in enumerate(board) if value]
    # Keep the AI responsive even with a large lexicon or densely populated board.
    budget={"casual":0.6,"standard":0.75,"challenging":1.5}[difficulty]
    scan_limit={"casual":250,"standard":1100,"challenging":3000}[difficulty]
    max_checks={"casual":50,"standard":250,"challenging":700}[difficulty]
    start=time.monotonic()
    best, best_score=None,float("-inf")
    checks=0
    candidates=0
    board_letters=Counter(char for _,char in anchors)
    if anchors:
        anchors.sort(key=lambda a:abs(a[0]//15-7)+abs(a[0]%15-7))
        anchors=anchors[:20]
    for word in _words():
        if time.monotonic()-start>budget or candidates>=scan_limit or checks>=max_checks:
            break
        need=Counter(word)-stock
        if sum(need.values())>stock["?"]+(2 if anchors else 0):
            continue
        if anchors and any(need[c]>stock["?"]+board_letters[c] for c in need):
            continue
        candidates+=1
        trials=[]
        if not anchors:
            for horizontal in (True,False):
                step=1 if horizontal else SIZE
                for offset in range(len(word)):
                    first=112-step*offset
                    if first>=0 and (first%SIZE if horizontal else first//SIZE)+len(word)<=SIZE:
                        trials.append((first,step,horizontal))
        else:
            for spot,letter in anchors:
                for at,char in enumerate(word):
                    if letter!=char:
                        continue
                    for horizontal in (True,False):
                        step=1 if horizontal else SIZE
                        first=spot-step*at
                        if first<0 or (first%SIZE if horizontal else first//SIZE)+len(word)>SIZE:
                            continue
                        trials.append((first,step,horizontal))
        # The candidate ranking prioritizes high-valued rack words, while
        # evaluation uses the exact in-game premium and cross-word scoring.
        for first,step,horizontal in trials:
            if checks>=max_checks or time.monotonic()-start>budget:
                break
            arranged=_word_with_blanks(word,first,step,board,stock)
            if arranged is None:
                continue
            checks+=1
            try:
                start_cell=chr(65+first%SIZE)+str(first//SIZE+1)
                action=make_placement(state,actor,start_cell,"H" if horizontal else "V",arranged)
                resulting=game.apply_action(state,players,actor,action)
                score=resulting["scores"][players.index(actor)]-state["scores"][players.index(actor)]
                # Casual accepts an early legal word rather than optimizing.
                if difficulty=="casual":
                    return action
                if score>best_score:
                    best,best_score=action,score
            except (ValueError,IndexError):
                continue
    if best is not None:
        return best
    # No legal placement found within the budget. Exchange on a nonempty bag
    # to improve the rack without getting trapped in a perpetual pass loop.
    from .games.scrabble import _tile_counts
    if sum(_tile_counts(state))>=7:
        slots=[i for i,v in enumerate(rack) if v!=EMPTY_RACK]
        if slots:
            mask=sum(1<<i for i in slots[:min(3,len(slots))])
            return bytes((0x80|mask,))
    return b"\x01"
