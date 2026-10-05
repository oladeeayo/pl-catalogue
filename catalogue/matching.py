"""Shared FPL player-name matching helpers (used by enrich_* modules).

Resolution layers: exact normalized keys (full / first+web / web surname),
then a conservative surname + first-name-prefix heuristic (unique hits only).
"""
import html
import re
import sqlite3
import unicodedata

# non-prefix nickname pairs (extendable, verified per use)
NICK_FIRST = {"kostas": "konstantinos"}

LETTERS = {"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
           "æ": "ae", "Æ": "AE", "ß": "ss", "œ": "oe", "Œ": "OE"}


def norm(s):
    s = html.unescape(str(s or "")).strip()
    for k, v in LETTERS.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def prefix_pair(a, b):
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    return a.startswith(b) or b.startswith(a)


class FPLIndex:
    """Name/code resolution indexes over the players table."""

    def __init__(self, con=None, db_path=None):
        if con is None:
            con = sqlite3.connect(db_path)
            self._owned = True
        else:
            self._owned = False
        self.con = con
        self.by_code = {}
        self.by_name = {}
        self.first_tok = {}
        self.by_last = {}
        cur = con.execute(
            "SELECT player_id, fpl_code, first_name, second_name, web_name FROM players")
        for pid, code, first, second, web in cur:
            if code is not None:
                self.by_code[str(int(code))] = int(pid)
            for key in (norm(f"{first} {second}"), norm(f"{first} {web}"), norm(web)):
                if key:
                    self.by_name.setdefault(key, []).append(int(pid))
            f = norm(first)
            if f:
                self.first_tok[int(pid)] = f.split()[0]
            for field in (second, web):
                v = norm(field)
                if v and len(v.split()[-1]) > 2:
                    self.by_last.setdefault(v.split()[-1], set()).add(int(pid))

    def close(self):
        if self._owned:
            self.con.close()

    def resolve_name(self, name):
        n = norm(name)
        if n:
            cands = set(self.by_name.get(n, []))
            if len(cands) == 1:
                return next(iter(cands))
            pid = self.loose(n)
            if pid is not None:
                return pid
        return None

    def loose(self, fbname):
        toks = fbname.split()
        if len(toks) < 2:
            return None
        t_first = NICK_FIRST.get(toks[0], toks[0])
        hits = {pid for pid in self.by_last.get(toks[-1], set())
                if prefix_pair(t_first, self.first_tok.get(pid, ""))}
        return next(iter(hits)) if len(hits) == 1 else None
