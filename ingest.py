"""Load a books CSV file into books.db, following the rules in rules.md.

Usage:
    py ingest.py books.csv
    py ingest.py books.csv --db test.db --log test-log.json   (used by the tests)

By default books.db and ingestion-log.json are made in the same folder as this
script (rules.md answer 1). No AI and no internet: the same file always gives
the same result.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]

# Labels (rule 6 and answers 7, 17)
LETTER = "Wrong ISBN: contains a letter or symbol"
DIGITS = "Wrong ISBN: wrong number of digits ({})"
PREFIX = "Wrong ISBN: does not start with 978 or 979"
CHECK = "Wrong ISBN: check digit fails"
MISSING = "Missing ISBN"
REPAIRED = "Repaired"
NO_MATCH = "Warning ISBN-10 and ISBN-13 do not match"
DUPLICATE = "Warning duplicate ISBN: also used by book_id {}"
KEPT_EMPTY = "Warning: new delivery had no ISBN, kept previous"
KEPT_WRONG = "Warning: new delivery had a wrong ISBN, kept previous"
CHECK_BOTH = "Warning: check ISBN numbers for correct one"
NO_BOOK_ID = "Warning: missing book_id"

# What to do, by kind of note (rule 11 and answer 28)
FIND_ISBN = "find the correct ISBN on the book or from the publisher"
WHICH_BOOK = "check which book the ISBN belongs to"
WHICH_ISBN = "check which ISBN is correct on the book or from the publisher"
PREVIOUS = "check whether the previous ISBN is still correct"
FIND_ID = "find the book's book_id"


def what_to_do_for(label):
    if label.startswith("Wrong ISBN") or label == MISSING:
        return FIND_ISBN
    if label.startswith("Warning duplicate ISBN"):
        return WHICH_BOOK
    if label in (NO_MATCH, CHECK_BOTH):
        return WHICH_ISBN
    if label in (KEPT_EMPTY, KEPT_WRONG):
        return PREVIOUS
    if label == NO_BOOK_ID:
        return FIND_ID
    return None


# ---------- Checking an ISBN (rules 4 and 5) ----------

def isbn10_shape(s):
    return re.fullmatch(r"\d{9}[\dX]", s) is not None


def isbn10_check(s):
    return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)) % 11 == 0


def valid_isbn10(s):
    return isbn10_shape(s) and isbn10_check(s)


def isbn13_check(s):
    return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s)) % 10 == 0


def valid_isbn13(s):
    return re.fullmatch(r"97[89]\d{10}", s) is not None and isbn13_check(s)


def valid_for(column, s):
    return valid_isbn10(s) if column == "isbn" else valid_isbn13(s)


def isbn10_to_13(s):
    first12 = "978" + s[:9]
    return first12 + str((10 - sum(int(c) * (1 if i % 2 == 0 else 3)
                                   for i, c in enumerate(first12)) % 10) % 10)


# ---------- Formatting repairs and labels (rule 3, answers 8-10) ----------

def clean(value):
    """Step a: remove hyphens and spaces, lowercase x becomes capital X."""
    return re.sub(r"[\s-]", "", value).replace("x", "X")


def candidate(column, value):
    """Steps a and b: the value as it would be stored if it turns out valid."""
    s = clean(value)
    if column == "isbn" and re.fullmatch(r"\d*X?", s) and 0 < len(s) < 10:   # answers 8b and 9
        s = s.zfill(10)
    return s


def problem_labels(column, value):
    """Step d: label the problems found in the cleaned value."""
    s = clean(value)
    padded = candidate(column, value)
    right_length = 10 if column == "isbn" else 13
    digits = re.sub(r"\D", "", s)
    labels = []

    body = s[:-1] if (len(s) == 10 and s.endswith("X")) else s   # X only as an ISBN-10's last character
    if re.search(r"\D", body):
        labels.append(LETTER)
    # Check digit, tested only when the value has the right shape after step b
    if column == "isbn" and isbn10_shape(padded) and not isbn10_check(padded):
        labels.append(CHECK)
    if column == "isbn13" and re.fullmatch(r"\d{13}", s) and not isbn13_check(s):
        labels.append(CHECK)
    # Digit count only when the value has the wrong number of characters (answer 8 follow-up)
    if len(s) != right_length:
        labels.append(DIGITS.format(len(digits)))
    if column == "isbn13" and digits and not digits.startswith(("978", "979")):
        labels.append(PREFIX)
    return labels


def check_isbns(raw10, raw13):
    """Decide what is stored in isbn and isbn13 and the labels for each.

    Returns (isbn, isbn13, labels, order, good): labels is {"isbn": [...], "isbn13": [...]},
    order says which column's note comes first, and good says which value is a valid ISBN.
    """
    labels = {"isbn": [], "isbn13": []}
    stored = {"isbn": raw10, "isbn13": raw13}
    good = {"isbn": False, "isbn13": False}
    c10, c13 = candidate("isbn", raw10), candidate("isbn13", raw13)

    # Rule 3: an ISBN-13 in the isbn column, or an ISBN-10 in the isbn13 column
    moved = None
    if raw10 != "" and valid_isbn13(clean(raw10)) and not valid_isbn10(c10):
        moved = ("isbn", "isbn13", clean(raw10))
    elif raw13 != "" and valid_isbn10(clean(raw13)) and not valid_isbn13(c13):
        moved = ("isbn13", "isbn", clean(raw13))
    if moved:
        source, target, value = moved
        if stored[target] == "":
            stored[target], stored[source] = value, ""
            labels[target].append(REPAIRED)
            labels[source].append(MISSING)
            good[target] = True
            return stored["isbn"], stored["isbn13"], labels, [target, source], good
        labels["isbn"].append(CHECK_BOTH)
        labels["isbn13"].append(CHECK_BOTH)
        return raw10, raw13, labels, ["isbn", "isbn13"], good

    for column, raw, cand in (("isbn", raw10, c10), ("isbn13", raw13, c13)):
        if raw == "":
            labels[column].append(MISSING)
        elif valid_for(column, cand):
            stored[column] = cand
            good[column] = True
            if cand != raw:
                labels[column].append(REPAIRED)
        else:
            labels[column] = problem_labels(column, raw)

    # Answer 11: only when both are valid; the note goes on isbn13
    if good["isbn"] and good["isbn13"] and isbn10_to_13(stored["isbn"]) != stored["isbn13"]:
        labels["isbn13"].append(NO_MATCH)
    return stored["isbn"], stored["isbn13"], labels, ["isbn", "isbn13"], good


def format_note(labels, order, extra=()):
    parts = [f"{col}: " + "; ".join(labels[col]) for col in order if labels[col]]
    parts.extend(extra)
    return "; ".join(parts)


def note_labels(note):
    """Split a stored note back into (column or None, label) pairs, in order."""
    out, current = [], None
    for part in (note or "").split("; "):
        if not part:
            continue
        if part.startswith("isbn13: "):
            current, part = "isbn13", part[len("isbn13: "):]
        elif part.startswith("isbn: "):
            current, part = "isbn", part[len("isbn: "):]
        elif part.startswith("Updated:") or part == NO_BOOK_ID:
            current = None
        out.append((current, part))
    return out


def what_to_do(note):
    """Answer 28: one 'what to do' per kind of note, in note order."""
    actions = []
    for _, label in note_labels(note):
        action = what_to_do_for(label)
        if action and action not in actions:
            actions.append(action)
    return actions


def needs_attention(note):
    """Answer 19: the isbn has a problem, or the row has any Warning."""
    for column, label in note_labels(note):
        if label.startswith("Warning"):
            return True
        if column == "isbn" and (label.startswith("Wrong ISBN") or label == MISSING):
            return True
    return False


def isbn13_only_problem(note):
    """Answer 19: a row whose only problems are in the isbn13."""
    return (not needs_attention(note)
            and any(c == "isbn13" and (l.startswith("Wrong ISBN") or l == MISSING)
                    for c, l in note_labels(note)))


# ---------- Database ----------

def open_db(path):
    con = sqlite3.connect(path)
    cols = ", ".join(f'"{c}" TEXT' for c in COLUMNS)
    con.execute(f"CREATE TABLE IF NOT EXISTS books ({cols}, changes_repairs TEXT)")   # answers 3-5
    con.execute("CREATE INDEX IF NOT EXISTS books_book_id ON books(book_id)")
    return con


def other_book_with(con, column, value, book_id):
    """Rule 10 and answers 12-13: another book with the same good ISBN in the same column."""
    for other_id, other_value in con.execute(
            f'SELECT book_id, "{column}" FROM books WHERE "{column}" = ? ORDER BY rowid', (value,)):
        if other_id != book_id and other_id != "" and valid_for(column, other_value):
            return other_id
    return None


# ---------- One run ----------

def process(con, path, entry):
    counts = entry["counts"]
    seen_this_file = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        for values in reader:
            line = reader.line_num            # answer 26: the header is line 1
            counts["read"] += 1
            record = {c: "" for c in COLUMNS}  # answer 2: a missing column is stored as empty
            for name, value in zip(header, values):
                if name in record:
                    record[name] = value
            book_id = record["book_id"]

            isbn, isbn13, labels, order, good = check_isbns(record["isbn"], record["isbn13"])
            row = dict(record, isbn=isbn, isbn13=isbn13)

            # Answer 7: a row with no book_id is never the same book as another row
            if book_id == "":
                note = format_note(labels, order, [NO_BOOK_ID])
                insert(con, row, note)
                counts["loaded"] += 1
                record_outcome(entry, line, record, note)
                continue

            # Rule 9: twice in the same file, load the first and skip the second
            if book_id in seen_this_file:
                counts["skipped"] += 1
                entry["not_loaded"].append({"line": line, "book_id": book_id, "title": record["title"],
                                            "reason": "appeared twice in this file"})
                continue
            seen_this_file.add(book_id)

            stored_row = None
            stored = con.execute("SELECT rowid, * FROM books WHERE book_id = ?", (book_id,)).fetchone()
            kept = False
            if stored:
                stored_row = dict(zip(["rowid"] + COLUMNS + ["changes_repairs"], stored))
                # Rule 9 and answers 17-18: never replace a good ISBN with an empty or Wrong one
                for column in ("isbn", "isbn13"):
                    old = stored_row[column]
                    if valid_for(column, old) and not good[column]:
                        labels[column] = [KEPT_EMPTY if row[column] == "" else KEPT_WRONG]
                        row[column] = old
                        good[column] = True
                        kept = True

            # Rule 10: duplicate ISBNs, valid ones only, same column only
            for column in ("isbn", "isbn13"):
                if good[column] and row[column]:
                    other = other_book_with(con, column, row[column], book_id)
                    if other is not None:
                        labels[column].append(DUPLICATE.format(other))

            note = format_note(labels, order)

            if stored_row is None:
                insert(con, row, note)
                counts["loaded"] += 1
                record_outcome(entry, line, record, note)
                continue

            # Answer 14: identical means all 23 columns are equal after repairs
            changed = [c for c in COLUMNS if row[c] != stored_row[c]]
            if not changed and not kept:
                counts["skipped"] += 1
                entry["not_loaded"].append({"line": line, "book_id": book_id, "title": record["title"],
                                            "reason": "already exists"})
                continue

            # Answers 15-16: the new delivery's notes plus "Updated:" and the changed columns
            if changed:
                note = format_note(labels, order, ["Updated: " + ", ".join(changed)])
            con.execute(
                "UPDATE books SET " + ", ".join(f'"{c}" = ?' for c in COLUMNS)
                + ", changes_repairs = ? WHERE rowid = ?",
                [row[c] for c in COLUMNS] + [note, stored_row["rowid"]])
            counts["updated"] += 1
            record_outcome(entry, line, record, note)


def insert(con, row, note):
    con.execute(
        "INSERT INTO books (" + ", ".join(f'"{c}"' for c in COLUMNS) + ", changes_repairs) VALUES ("
        + ", ".join("?" * (len(COLUMNS) + 1)) + ")",
        [row[c] for c in COLUMNS] + [note])


def record_outcome(entry, line, record, note):
    """Counts and lists for a row loaded or updated in this run (answers 19, 21, 25)."""
    entry["counts"]["repairs"] += sum(1 for _, label in note_labels(note) if label == REPAIRED)
    if needs_attention(note):
        entry["needs_attention"].append({
            "line": line, "book_id": record["book_id"], "title": record["title"],
            "isbn_as_arrived": record["isbn"], "isbn13_as_arrived": record["isbn13"],
            "note": note, "what_to_do": what_to_do(note),
        })
    elif isbn13_only_problem(note):
        entry["counts"]["isbn13_only_problems"] += 1


def still_to_fix(con):
    """Rule 15: every book in the database still needing a person, whichever run found it."""
    out = []
    for book_id, title, isbn, isbn13, note in con.execute(
            "SELECT book_id, title, isbn, isbn13, changes_repairs FROM books ORDER BY rowid"):
        if needs_attention(note):
            out.append({"book_id": book_id, "title": title, "isbn": isbn, "isbn13": isbn13,
                        "note": note, "what_to_do": what_to_do(note)})
    return out


# ---------- Log (answer 23, rule 15) ----------

def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        runs = json.load(f)   # a damaged log stops the run rather than being overwritten
    if not isinstance(runs, list):
        raise ValueError(f"{path} is not a list of runs; not overwriting it")
    return runs


def append_log(path, entry):
    runs = read_log(path)
    runs.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load a books CSV file into a SQLite database.")
    parser.add_argument("file", help="the books CSV file to load")
    parser.add_argument("--db", default=os.path.join(HERE, "books.db"))
    parser.add_argument("--log", default=os.path.join(HERE, "ingestion-log.json"))
    args = parser.parse_args(argv)

    # Answer 2: a missing file stops with an error, changes nothing, and adds no log entry
    if not os.path.isfile(args.file):
        print(f"Error: {args.file} does not exist. Nothing was changed.")
        return 1
    read_log(args.log)

    entry = {
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),   # answer 27
        "file": os.path.basename(args.file),
        "finished": False,
        "counts": {"read": 0, "loaded": 0, "updated": 0, "skipped": 0, "rejected": 0,
                   "repairs": 0, "isbn13_only_problems": 0},
        "needs_attention": [],
        "not_loaded": [],
        "still_to_fix": [],
    }

    con = open_db(args.db)
    try:
        with con:   # section 14: a crash undoes the whole run
            process(con, args.file, entry)
        entry["finished"] = True
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    entry["still_to_fix"] = still_to_fix(con)
    con.close()
    append_log(args.log, entry)

    c = entry["counts"]
    print(f"{entry['file']}: read {c['read']}, loaded {c['loaded']}, updated {c['updated']}, "
          f"skipped {c['skipped']}, rejected {c['rejected']}, repairs {c['repairs']}, "
          f"rows needing attention {len(entry['needs_attention'])}")
    print("finished" if entry["finished"] else "NOT finished: " + entry["error"])
    return 0 if entry["finished"] else 1


if __name__ == "__main__":
    sys.exit(main())
