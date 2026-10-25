# PanBank v3.1 (by G. Tsakalos)

## Features

PanBank is an **offline single-user personal finance manager** built with Python, PySide6 (Qt), and SQLite. Its primary accounting method is **manually entered month-end account snapshots**; it does not infer bank balances from individual transactions.

### Dashboard and monthly reconciliation
- Select a month and year to view opening and closing source balances, registered income, registered expenses, and estimated unregistered daily expenses.
- Calculate estimated unregistered expenses using the original rule: **previous month-end sources − current month-end sources + current-month registered incomes − current-month registered expenses**.
- Review all 12 months in a yearly table, including opening/closing snapshot counts. A warning appears when opening or closing month snapshots are absent; partially missing account snapshots require manual checking.

### Transactions, accounts, and budgets
- Add, edit, delete, filter, and export records in the **Income** and **Expense** registers. Dated records appear newest first.
- Maintain **Accounts**, **Payment methods**, and manually recorded **Monthly snapshots** as separate registers. Payment methods can be linked to accounts without treating them as income or expenses.
- Maintain **annual income and expense budgets** and compare planned amounts with recorded actual transactions in **Budget vs Actual**.
- Organize expense categories into **root categories and second-level subcategories**. Expense transactions and expense budgets must use a second-level category; root totals are calculated from subcategories.
- Manage category lists and other reference records through the Settings and relevant register tabs.

### Search, filtering, and charts
- Apply a **Filter** to each editable register to narrow displayed records by field and text.
- Use **Global Search** to find records across registers using free-text search, an optional exact EUR amount, and a register selector; clear the search with one click.
- Display six selectable charts: monthly income vs registered expenses, estimated unregistered expenses, month-end source balances, annual income vs expenses, expenses by root category, and budget vs actual expenses. Choose the reporting year where applicable.

### Investment and debt management
- Register investment assets, **Buy / Sell / Dividend / Income / Fee** transactions, and manual price valuations.
- View a **Portfolio** summary with held units, FIFO cost basis, realized and unrealized gains/losses, distributions, and warnings for sales exceeding available units.
- Track **taxes, loans, and other debts** with one-time or monthly installment schedules, record actual repayments, and view the remaining amount and projected due dates.

### Data management and portability
- Keep data in a local **SQLite** database (`panbank.sqlite3`), created empty beside `panbank.py` by default. Select another database folder from the GUI if desired.
- **Import and export CSV** from individual registers. Income and expense CSV imports support the documented semicolon-delimited, human-readable templates; other registers use database-field column names.
- Optionally import selected register sheets from an existing Excel workbook after preview and confirmation; no source workbook records are preloaded.
- Export all database tables into a new **Excel workbook** and create an SQLite database backup from the **Data & Backups** tab.
- Access financial-logic guidance from the **Help** menu.

**Scope:** PanBank does not automatically synchronize bank transactions, market prices, or exchange rates. The SQLite database and backup files are not encrypted by the app.

# PanBank - Python / PySide6 / SQLite

A single-user, offline personal finance assistant desktop app for Windows & Linux.

## Install & run

Python 3.11 or later (3.14 is supported if compatible wheels for PySide6/openpyxl exist already):

```bash
cd PanBank
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe panbank.py
```

Ubuntu MATE:

```bash
sudo apt install python3-venv libxcb-cursor0
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python panbank.py
```

On Ubuntu MATE, the application is a native Qt/X11 desktop window. No Snap packages or server are required.

## Database storage

The application **creates a new empty SQLite database** on the first run. No records from `PanBankClear.xlsx` are included in the project, in the database schema, or in a seed file.

**Default (Windows and Linux):** `panbank.sqlite3` is stored **beside `panbank.py`** in the application folder. You can keep the application and its database together on a portable drive, provided the folder is writable.

In **Data & Backups**, click **Select database folder…** to switch to an existing database folder or create a fresh empty one. **Use application folder (default)** returns to the portable location. Your choice persists in `panbank_settings.json` next to the script; returning to default deletes that settings file. The optional `PANBANK_HOME` environment variable takes precedence at startup.

**Important for upgrades:** If you replace the *entire* PanBank folder, first back up your database and `panbank_settings.json`. Prefer replacing only the program files while keeping your SQLite database intact. Changing the database folder **does not move your old database or automatically import its records**. To relocate existing records, close PanBank and copy `panbank.sqlite3` (using a backup made from the app is safer), then select its destination folder.

If upgrading from v0.1, the old database in your user-profile directory will remain there: the new app will not automatically find or migrate it. Use the new folder selector to point at that existing directory, or back up and relocate it.

The `Data & Backups` tab supports safe SQLite backups via SQLite's backup API. Close the application before replacing the live SQLite file with a backup.

## Recommended starting sequence

1. Create **Categories** for income and expenses. Use `kind=income` or `kind=expense`. A category can optionally belong to an existing parent category. Categories referenced by records cannot be deleted until those references are removed. You may set `Active = off` instead.
2. Create **Accounts**, using stable names matching source names in your workbook if future import is intended.
3. Create **Payment methods** and associate each card with an account when appropriate.
4. Enter **Annual budgets**: one row for each year/type/category. The summary compares those plans with registered actual transactions in the same year.
5. Enter **Income register** and **Expense register** records.
6. Enter **Monthly snapshots**: for each account and month, provide a snapshot date and manually determined account balance. Enter the previous month's snapshot as well so the first monthly reconciliation has an opening total.
7. Enter **Investment assets**, **Investment transactions**, and optional dated **Investment valuations**.
8. Enter **Debts** and **Debt payments** to track taxes, loans and payment schedules.

## Monthly estimated daily expenses

The app deliberately keeps the original snapshot-based formula **without** additional adjustments:

```
unregistered_daily_expenses = opening_month_source_balances
                            - closing_month_source_balances
                            + registered_month_incomes
                            - registered_month_expenses
```

The balances use snapshot rows recorded for the preceding month and selected month respectively. For each account the latest dated snapshot in that month is used. All account balances are **manually entered**. The app does not compute balances from transactions.

**Important:** For a valid comparison, record the same set of financial sources at each month-end. The dashboard warns if either comparison month has no snapshots. It does not yet check for a *partially completed* month (e.g., one out of three accounts missing): always review the displayed snapshot counts. Missing snapshot months are not reliable zero balances. Transfers to investments, credit-card changes, or sources outside the snapshot set can distort the estimate; the formula is displayed as explicitly requested.

## Income & expense accounting

Registered expenses reduce the estimated unregistered amount through the monthly formula, but do **not** automatically adjust source snapshots. A payment medium (PayPal, debit card, credit card) is distinct from a financial source. A credit card payment is not automatically recorded as a second expense; avoid double-recording the same underlying purchase. There is no auto-generated transaction posting from investments or debt payments.

## Investments

- Each investment `Buy` or `Sell` transaction requires a positive **Units** and **Unit price**. Fees are nonnegative EUR amounts. Transactions are sorted by date then ID.
- Profit/loss is calculated using FIFO purchase lots. Fees increase acquisition cost and reduce sale proceeds.
- Sales of more units than available are flagged by the portfolio report and are excluded from realized gain calculations.
- `Dividend` and `Income` actions calculate income as `units × unit_price − fees`; `Fee` actions reduce reported realized return.
- Latest **Investment valuations** are manually entered prices; unrealized gain/loss equals held units × latest unit price minus FIFO cost basis.
- This is a portfolio-tracking calculation, not a tax-lot certification or exchange-rate conversion service. Investment cash movements are not automatically part of the monthly source snapshot calculation.

## Debts

Create one debt per tax or loan. `Total owed`, `No. of installments`, and `First due date` determine an *equal monthly* installment schedule. One-time payments use one installment. The Debt payments table records actual payments and reduces the displayed remaining balance. The schedule is indicative: extra payments do not reschedule future installments automatically, and due dates are calculated monthly (not adjusted for weekends/holidays). Register paid amounts in Expense register as well if they are expenses you wish to count in the monthly formula. Do not double-count principal transfers that are not expenses.

## Import/export

### Optional original Excel importer

Click `Data & Backups → Import from original Excel` and select your original workbook explicitly. The import dialog supports:

| Sheet | Destination | Details |
|---|---|---|
| `A_Incomes` | incomes | Date, Amount, Group, Providers, Notes |
| `B_Expences` | expenses | Date, Amount, Group, Notes, PaymentSource, Median, Seller, InvoiceTo, Fail? |
| `C_Sources` | snapshots | Date, Source, Amount, Notes |
| `D_Invests` | investments | Buy/sell units, price, fees, asset, date, remarks |

The importer **only inserts records after you explicitly select and confirm the sheet**, and it never automatically populates categories, accounts or assets. Lookup names in the Excel must exactly match names already created in PanBank. It previews successful rows and errors and refuses to import any sheet that still has rejected rows. Imports are transactional; an SQL failure rolls the whole batch back. Importing again duplicates records unless uniqueness constraints prevent it. The workbook itself is never edited.

Annual budgets and other Excel summary sheets are **not mapped** by the legacy importer (layouts vary); enter budgets directly or use the application's CSV editor import. The importer's objective is reliable register migration, not workbook formula copying.

### CSV

Every register has `Export CSV` and `Import CSV` buttons. Export a blank/filled register first to obtain the *exact column header names* required for re-import. Foreign-key fields such as `category_id`, `account_id`, `asset_id` use the numeric IDs already present in the destination database. Dates use `YYYY-MM-DD`, monetary values use `123.45` (EUR), and UTF-8 with BOM is used for Windows Excel compatibility. Import is insert-only and transactional, with explicit confirmation.

### Excel export

`Data & Backups → Export all tables to Excel` produces a new `.xlsx` workbook, one sheet per database table, with editable raw-record export (not a reconstruction of the original formula-driven Excel file).

## Limitations / future improvements

The app currently targets EUR monetary amounts and does not automatically download bank feeds, prices, or FX rates. Dates, units and numerical amounts are validated to varying degrees; automated unit tests should be extended before importing a large dataset. The legacy Excel importer does not yet migrate complex tax-history and planning summaries. The snapshot dashboard treats missing balances as missing information rather than inferring them from income or expenses. Debt payment amounts are not matched to specific installments. Additions such as search/filter controls on large tables, partial-month completeness validation, FX conversion, transaction-to-debt linking, and richer charts are natural next releases.

For sensitive account information, protect your computer login and disk. SQLite database files and backups are not encrypted by this application.
