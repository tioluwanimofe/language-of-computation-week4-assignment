"""Tests that ingest.py does what rules.md says.

Every test runs ingest.py in its own temporary folder, on a database named test.db
and a log named test-log.json, and removes the folder afterwards, pass or fail.
The books.db and ingestion-log.json in this folder are never touched.

Run with:  py -m pytest -v
"""

import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.join(HERE, "ingest.py")
BATCH2 = os.path.join(HERE, "books-batch2.csv")

COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]

WRONG_OR_MISSING = "find the correct ISBN on the book or from the publisher"
DUPLICATE = "check which book the ISBN belongs to"


# ---------- setup and teardown ----------

@pytest.fixture
def workdir():
    """A fresh folder for test.db, test-log.json and test CSV files, removed afterwards."""
    path = tempfile.mkdtemp(prefix="ingest-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def make_row(book_id, isbn, isbn13, title=None):
    row = {c: "1" for c in COLUMNS}
    row.update(book_id=book_id, isbn=isbn, isbn13=isbn13, title=title or f"Book {book_id}",
               original_title=title or f"Book {book_id}", authors="Test Author",
               language_code="eng", image_url="", small_image_url="")
    return row


def write_csv(folder, name, rows):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def run_ingest(folder, csv_path, db_name="test.db"):
    db = os.path.join(folder, db_name)
    log = os.path.join(folder, "test-log.json")
    result = subprocess.run(
        [sys.executable, INGEST, csv_path, "--db", db, "--log", log],
        cwd=folder, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return db, log


def books(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM books")]
    con.close()
    return rows


def book(db, book_id):
    matches = [b for b in books(db) if b["book_id"] == book_id]
    assert len(matches) == 1, f"expected one book {book_id}, found {len(matches)}"
    return matches[0]


def read_log(log):
    with open(log, encoding="utf-8") as f:
        return json.load(f)


def notes_for(changes_repairs, column):
    """The labels a note gives one ISBN column (rule 2: each note starts with 'isbn:' or 'isbn13:')."""
    labels, current = [], None
    for part in (changes_repairs or "").split("; "):
        if part.startswith("isbn13: "):
            current, part = "isbn13", part[len("isbn13: "):]
        elif part.startswith("isbn: "):
            current, part = "isbn", part[len("isbn: "):]
        elif part.startswith("Updated:") or part.startswith("Warning: missing book_id"):
            current = None
            continue
        if current == column and part:
            labels.append(part)
    return labels


def attention_row(entry, book_id):
    rows = [r for r in entry["needs_attention"] if r["book_id"] == book_id]
    return rows[0] if rows else None


def ingest_one(folder, isbn, isbn13, book_id="10001"):
    csv_path = write_csv(folder, "one.csv", [make_row(book_id, isbn, isbn13)])
    db, log = run_ingest(folder, csv_path)
    return book(db, book_id), read_log(log)[-1]


# ---------- one test per example in rules.md section 12 ----------
# Each example is about one column. The other column holds a valid ISBN that matches,
# so the notes checked are only those the example describes.

def test_example_01_lost_leading_zero(workdir):
    b, entry = ingest_one(workdir, "439023483", "9780439023481")
    assert b["isbn"] == "0439023483"
    assert notes_for(b["changes_repairs"], "isbn") == ["Repaired"]
    assert attention_row(entry, "10001") is None          # not listed in the report
    assert entry["counts"]["repairs"] == 1                # counted in the repairs total


def test_example_02_check_digit_fails(workdir):
    b, entry = ingest_one(workdir, "1854433017", "9781854433015")
    assert b["isbn"] == "1854433017"
    assert notes_for(b["changes_repairs"], "isbn") == ["Wrong ISBN: check digit fails"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_03_two_zeros_added(workdir):
    b, _ = ingest_one(workdir, "61120081", "9780061120084")
    assert b["isbn"] == "0061120081"
    assert notes_for(b["changes_repairs"], "isbn") == ["Repaired"]


def test_example_04_hyphens_removed(workdir):
    b, _ = ingest_one(workdir, "0-450-19827-8", "9780450198274")
    assert b["isbn"] == "0450198278"
    assert notes_for(b["changes_repairs"], "isbn") == ["Repaired"]


def test_example_05_spaces_removed(workdir):
    b, _ = ingest_one(workdir, " 162 001 4750 ", "9781620014752")
    assert b["isbn"] == "1620014750"
    assert notes_for(b["changes_repairs"], "isbn") == ["Repaired"]


def test_example_06_lowercase_x(workdir):
    b, _ = ingest_one(workdir, "031200009x", "9780312000097")
    assert b["isbn"] == "031200009X"
    assert notes_for(b["changes_repairs"], "isbn") == ["Repaired"]


def test_example_07_zero_not_kept_when_check_fails(workdir):
    b, entry = ingest_one(workdir, "385535144", "9780385535144")
    assert b["isbn"] == "385535144"
    assert notes_for(b["changes_repairs"], "isbn") == [
        "Wrong ISBN: check digit fails", "Wrong ISBN: wrong number of digits (9)"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_08_letter(workdir):
    b, entry = ingest_one(workdir, "04390A3483", "9780439023481")
    assert b["isbn"] == "04390A3483"
    assert notes_for(b["changes_repairs"], "isbn") == ["Wrong ISBN: contains a letter or symbol"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_09_eleven_digits(workdir):
    b, entry = ingest_one(workdir, "15287463097", "9781528746304")
    assert b["isbn"] == "15287463097"
    assert notes_for(b["changes_repairs"], "isbn") == ["Wrong ISBN: wrong number of digits (11)"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_10_isbn13_in_isbn_column_is_moved(workdir):
    b, entry = ingest_one(workdir, "9781982147600", "")
    assert b["isbn13"] == "9781982147600"
    assert b["isbn"] == ""
    assert notes_for(b["changes_repairs"], "isbn13") == ["Repaired"]
    assert notes_for(b["changes_repairs"], "isbn") == ["Missing ISBN"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_11_scientific_notation(workdir):
    b, entry = ingest_one(workdir, "0439023483", "9.78043902348e+12")
    assert b["isbn13"] == "9.78043902348e+12"
    assert notes_for(b["changes_repairs"], "isbn13") == [
        "Wrong ISBN: contains a letter or symbol", "Wrong ISBN: wrong number of digits (14)"]
    # Answer 19: a row whose only problem is in isbn13 is counted at the top, not listed.
    assert entry["counts"]["isbn13_only_problems"] == 1


def test_example_12_scientific_notation_wrong_prefix(workdir):
    b, entry = ingest_one(workdir, "0439023483", "2.94001623192e+12")
    assert b["isbn13"] == "2.94001623192e+12"
    assert notes_for(b["changes_repairs"], "isbn13") == [
        "Wrong ISBN: contains a letter or symbol", "Wrong ISBN: wrong number of digits (14)",
        "Wrong ISBN: does not start with 978 or 979"]
    assert entry["counts"]["isbn13_only_problems"] == 1


def test_example_13_duplicate_isbn_on_another_book(workdir):
    csv_path = write_csv(workdir, "dup.csv", [
        make_row("9", "1416524797", "", title="Angels & Demons"),
        make_row("10013", "1416524797", "", title="Paper Moons"),
    ])
    db, log = run_ingest(workdir, csv_path)
    first, newcomer = book(db, "9"), book(db, "10013")        # both load
    assert newcomer["isbn"] == "1416524797"
    assert "Warning duplicate ISBN: also used by book_id 9" in notes_for(newcomer["changes_repairs"], "isbn")
    assert not any(n.startswith("Warning duplicate") for n in notes_for(first["changes_repairs"], "isbn"))
    row = attention_row(read_log(log)[-1], "10013")
    assert row is not None and DUPLICATE in row["what_to_do"]


def test_example_14_both_empty(workdir):
    b, entry = ingest_one(workdir, "", "")
    assert notes_for(b["changes_repairs"], "isbn") == ["Missing ISBN"]
    assert notes_for(b["changes_repairs"], "isbn13") == ["Missing ISBN"]
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


def test_example_15_empty_isbn_beside_979(workdir):
    b, entry = ingest_one(workdir, "", "9798351220413")
    assert b["isbn13"] == "9798351220413"
    assert notes_for(b["changes_repairs"], "isbn") == ["Missing ISBN"]
    assert notes_for(b["changes_repairs"], "isbn13") == []
    row = attention_row(entry, "10001")
    assert row is not None and WRONG_OR_MISSING in row["what_to_do"]


# ---------- runs, duplicates and the log ----------

def test_same_file_twice_creates_no_duplicates(workdir):
    db, _ = run_ingest(workdir, BATCH2)
    first = len(books(db))
    run_ingest(workdir, BATCH2)
    assert len(books(db)) == first
    ids = [b["book_id"] for b in books(db) if b["book_id"]]
    assert len(ids) == len(set(ids))


def test_every_run_appends_to_the_log(workdir):
    _, log = run_ingest(workdir, BATCH2)
    first_runs = read_log(log)
    assert len(first_runs) == 1
    run_ingest(workdir, BATCH2)
    runs = read_log(log)
    assert len(runs) == 2
    assert runs[0] == first_runs[0]          # the earlier entry is unchanged


def test_outcomes_add_up_to_rows_read(workdir):
    _, log = run_ingest(workdir, BATCH2)
    run_ingest(workdir, BATCH2)
    for entry in read_log(log):
        c = entry["counts"]
        assert c["read"] == 31
        assert c["loaded"] + c["updated"] + c["skipped"] + c["rejected"] == c["read"]
        assert c["rejected"] == 0            # rule 1: nothing is rejected


def test_same_file_on_empty_database_gives_same_database(workdir):
    db1, _ = run_ingest(workdir, BATCH2, db_name="test.db")
    first = sorted(books(db1), key=lambda b: json.dumps(b, sort_keys=True))
    os.remove(db1)
    db2, _ = run_ingest(workdir, BATCH2, db_name="test.db")
    second = sorted(books(db2), key=lambda b: json.dumps(b, sort_keys=True))
    assert first == second
