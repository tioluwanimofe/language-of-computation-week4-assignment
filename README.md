# Module 4 Assignment

This is my Module 4 assignment for MSAI-5523 (Language of Computing).

Partner: Melissa Forte

These steps are for this Windows computer, using PowerShell.

## Open the terminal and go to the folder

1. Press the **Windows key**, type `PowerShell`, and press **Enter**.
2. Go to the assignment folder:

   ```powershell
   cd $HOME\workspace\language-of-computation\week4-assignment
   ```

3. Check that Python works (it should print `Python 3.13.17` or similar):

   ```powershell
   py --version
   ```

## Install what the tests need

The tests use pytest. Install it once:

```powershell
py -m pip install --user pytest
```

## Run the tests

```powershell
py -m pytest -v
```

Each test runs `ingest.py` on its own `test.db` and `test-log.json` in a temporary folder and deletes that folder when it is done. The tests never touch `books.db` or `ingestion-log.json` in this folder.

To save what the tests print to a file:

```powershell
py -m pytest -v | Out-File -Encoding utf8 test-results.txt
```

## Run the ingestion

Start from an empty database and log (skip this if the files don't exist yet):

```powershell
Remove-Item books.db, ingestion-log.json
```

Then load the three deliveries in this order:

```powershell
py ingest.py books.csv
```

```powershell
py ingest.py books.csv
```

```powershell
py ingest.py books-batch2.csv
```

Each run prints its counts and adds an entry to `ingestion-log.json`.

## Open the report

1. Open the folder in File Explorer (type `explorer .` in the same PowerShell window).
2. Double-click `report.html`. It opens in your web browser; no server is needed.
3. Click **Open ingestion-log.json** and choose `ingestion-log.json` from this folder.
4. The runs appear newest first, each with its counts, the rows that need attention, the books still to fix, and the skipped rows.
