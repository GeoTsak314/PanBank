# PanBank Web v4.4.1

**Personal finance management · Python / FastAPI / SQLite · Single user**  
**by:** G. Tsakalos  
**Current stable Version 4 baseline:** v4.4.1

> This is the consolidated README for the **entire PanBank Web 4.x series, starting at v4.0**. It describes the **current v4.4.1 behavior first** and then documents how the application evolved through every significant 4.x release. Where an earlier version introduced behavior that was later revised, the v4.4.1 specification takes precedence.

## Contents

1. [Overview and architecture](#overview-and-architecture)
2. [Current features](#current-features)
3. [Financial calculation rules](#financial-calculation-rules)
4. [Windows installation and startup](#windows-installation-and-startup)
5. [Ubuntu Server and private access](#ubuntu-server-and-private-access)
6. [Database, configuration and backups](#database-configuration-and-backups)
7. [Data entry, searching and reporting](#data-entry-searching-and-reporting)
8. [Version history: v4.0–v4.4.1](#version-history-v40v441)
9. [Migration and upgrade notes](#migration-and-upgrade-notes)
10. [Limitations and validation](#limitations-and-validation)

## Overview and architecture

PanBank is a self-hosted, browser-based application for tracking personal income, expenses, accounts, investments, debts, monthly balances and annual budgets. It replaces the earlier **PySide6 desktop v3.2.x** application with a single web interface usable from Windows, Linux, and mobile browsers when the host is reachable securely.

| Component | Implementation |
|---|---|
| Web application | Python, FastAPI, Uvicorn, Jinja2 templates |
| Business/accounting logic | `core.py` |
| Application/routes | `panbank.py` |
| Data store | SQLite (`panbank.sqlite3` by default, or another selected existing file) |
| Settings and authentication | Local `panbank_settings.json`; browser-based login |
| PDF output | ReportLab and Beautiful Soup |
| CSV and other support | Python standard library and declared dependencies |
| Deployment | Single-user, local Windows process or private Linux server |

The database is **not** an online banking feed. Balances come from manually recorded account snapshots, and income/expense records are dated separately. The application does not alter an account balance automatically when you enter a transaction.

## Current features

- **Dashboard:** selected-month opening/closing snapshot balances, actual registered income, actual registered cash expenses/outflows, and estimated unregistered expenses; month-by-month table through the preceding completed calendar month; **SUMs** footer and five completed-month **average** cards; drill-down details.
- **Transactions:** Income register, Expense register, and Monthly snapshots. Record creation/editing uses pop-up windows; save closes the pop-up and refreshes the originating register. Date entry/display is `DD/MM/YYYY`.
- **Categories:** income category allocation remains selectable (`normal` or `annual_12`); expense subcategory analytical allocation is automatically annual `/12`; parent expense categories are aggregation-only. Category selection for expenses follows parent → child hierarchy.
- **Course → Budget vs Actual:** annual income and expense plans compared with actual categorized records; one income summary card and one registered-expenses summary card, each with Planned, Actual, Difference; separate informational expense parent-category totals; sign-aware color coding.
- **Investments:** asset register, Buy/Sell/Dividend/Income/Fee transactions, manual valuations, holdings/portfolio and FIFO reporting. Portfolio amounts are shown to two decimal places; units to six decimal places.
- **Debt:** debts, indicative installment schedule, repayments, and **Paid by other** flag. Personal repayments affect Dashboard cash expenses; third-party repayments reduce debt without affecting personal cash flows. Payments reduce the oldest unpaid installment first.
- **Registers:** sorting; text search and Excel-style checkbox filters; record-range selector for five large registers; multi-row delete with two confirmations and one atomic transaction.
- **Imports/exports:** semicolon CSV import/export for registers, SQLite backup, PDF export for supported reporting/register screens, and selected-database management.
- **Charts:** monthly income versus registered expenses, monthly unregistered expenses, closing balances, annual income versus expenses, expenses by parent category, and horizontal Budget vs Actual expenses.
- **Login:** local password-based session authentication. Windows launcher opens `/login`, which redirects already-authenticated users to the Dashboard.

## Financial calculation rules

### 1. Exact snapshot boundaries

For calendar month **M**, the **opening** amount is the sum of account snapshots dated **the first day of M**. The **closing** amount is the sum of snapshots dated **the first day of M+1**. For September 2026, the boundary dates are **01/09/2026** and **01/10/2026**.

Snapshots are entered manually and must cover a consistent set of accounts. Missing or partially entered boundaries can distort the calculation; the application cannot infer missing account balances from transaction registers.

### 2. The core reconciliation equation

```text
Unregistered expenses = Opening balances
                      - Closing balances
                      + Actual registered income
                      - Actual registered expenses / cash outflows
```

**Dashboard reports actual transaction-month flows, not annual `/12` allocations.** The registered income and expense terms include the additional cash movements listed below, on their actual dates.

| Record | Dashboard income | Dashboard registered expenses / outflows |
|---|---:|---:|
| Dated income record | Included | — |
| Dated expense record | — | Included |
| Investment Buy | — | Included: units × price + fees |
| Investment Sell | Included: units × price − fees | — |
| Investment Dividend or Income | Included: units × price − fees | — |
| Investment Fee | — | Included per investment cash-flow rules |
| Debt repayment, paid by user | — | Included |
| Debt repayment, **Paid by other** | — | **Excluded** |
| Annual budget | — | — |
| Monthly snapshot | Defines opening/closing balance | Not a transaction |

Investment sale proceeds are **cash inflows**, not realized profits. A transaction must not also be manually entered in the Income/Expense register if doing so would count the same cash flow twice. Debt payments treated as Dashboard expenses likewise should not be duplicated as standalone expense entries. Transfers between tracked accounts and inclusion of investment assets in snapshot balances must be handled consistently.

### 3. Dashboard period, totals, and averages

The selected month displays five actual measures: **Opening, Closing, Income, Registered expenses, and Unregistered expenses**.

For the **current year**, the month summary extends only through the **last fully completed calendar month**; historical years may show all 12 months. For example, on 08/11/2026 the current-year summary covers January–October 2026.

- **`SUMs (completed months)`** is the sum of each column's displayed monthly figures. Opening/closing SUMs are arithmetic sums of monthly stocks, *not* an annual start/end balance; interpret them only as inputs to the displayed averages.
- The five bottom **monthly averages** are each corresponding sum divided by the number of completed months.
- The selected-month cards, monthly table, and averages use **actual cash flows**, regardless of income category allocation settings.
- Separate year-reconciliation reporting may use endpoint snapshot logic; do not confuse it with summing opening/closing stocks.

### 4. Annual income/expense allocations

Annual smoothing is **analytical only** and does not change the date or amount of any transaction:

- **Income:** the allocation setting remains optional per income category (`normal` or `annual_12`). Annual-marked income is divided across all 12 months of its transaction year for allocation-oriented reporting.
- **Expenses:** *all second-level expense categories* use annual `/12` allocation for analytical reporting, regardless of earlier category flags. Parent categories do not contain directly counted expense transactions or budgets.
- Rounding is performed in cents so 12 allocated amounts sum to the original annual amount. Editing annual records may recalculate previously displayed allocation figures.
- **Dashboard v4.4.1 does not use these allocated figures in its actual-month reconciliation.** This replaces the early v4.0–v4.2 design, where allocated income/expenses could affect Dashboard figures.

### 5. Budget vs Actual

`Course → Budget vs Actual` compares **annual plans with actual categorized income and expense register records**. Investment/debt cash flows that lack budget category assignments are not automatically part of those category comparisons.

```text
Income difference  = Actual - Planned
Expense difference = Planned - Actual
```

The page displays the core unregistered-expense equation above two consolidated summary cards (Income; Registered expenses). **Positive differences are green, zero/negative differences red.** The main category table includes income categories and expense child categories; expense **parent-category totals appear separately below**, without double-counting in the summary boxes.

### 6. Debt schedule treatment

Debt schedules are installment projections. Recorded repayments are applied to the **oldest unpaid installment** first. Example: a €200 debt with €100 due in November and €100 due in December; a €50 payment on 08/11/2026 leaves **€50 due in November**, **€100 due in December**, and **€150 outstanding**. A repayment marked *Paid by other* still reduces the debt, but is excluded from the user's Dashboard expenses.

## Windows installation and startup

**Requirements:** A compatible installed Python interpreter and the dependencies from the package's `requirements.txt`.

```powershell
cd C:\PanBank
python -m pip install -r requirements.txt
python panbank.py --setup
```

On first setup, enter and confirm a login password, then choose the directory containing your existing database (or use the application directory). The password may be any length, although a strong, nonempty password is recommended. Setup saves a salted password hash and session secret in `panbank_settings.json`; it does **not** store the plaintext password.

After setup:

```powershell
cd C:\PanBank
python panbank.py
```

Then open **`http://127.0.0.1:8765/login`**. Alternatively, double-click the included **`StartWinPanBank.vbs`**, which starts the server without a visible console window and opens `/login`. If a valid session already exists, the login page redirects to the Dashboard.

**Important:** Stop the previous Python/Uvicorn process before replacing application files. The launcher can reuse an existing server, so copying new files alone may leave the old code running. The launcher maintains `panbank-launch.log` for diagnostics. If the browser shows *Internal Server Error*, check the traceback in that file or run `python panbank.py` in PowerShell.

## Ubuntu Server and private access

A basic first-time installation on Ubuntu:

```bash
sudo apt update
sudo apt install python3-venv python3-pip
mkdir -p ~/PanBankWeb
cd ~/PanBankWeb
# Copy application files here
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python panbank.py --setup
.venv/bin/python panbank.py
```

The default service binds to **`127.0.0.1:8765`**. For testing from a different computer, use an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 youruser@your-server
```

For private access from Windows, phone or tablet, prefer **Tailscale** with appropriate HTTPS/reverse-proxy configuration and host restrictions. Do **not** publicly forward port 8765 from the ISP router. Set `PANBANK_HTTPS=1` only when actually serving over HTTPS, otherwise the browser may refuse secure session cookies. For a persistent Ubuntu deployment, use a dedicated unprivileged systemd service account and protected directories; do not run the server as root.

## Database, configuration and backups

### Files to preserve

| File | Purpose |
|---|---|
| `panbank.sqlite3` or user-selected `.sqlite3` | Financial records |
| `panbank_settings.json` | Password hash, session secret, database selection and related local settings |
| `panbank-launch.log` (Windows) | Diagnostic log; can be recreated |
| `panbank.py`, `core.py`, `templates/` | Program code and UI; replace together when upgrading |

### Database Management

The **Settings → Database Management** page lets you select an **existing** SQLite file located in an approved server-side directory. PanBank validates the candidate database, stores the choice in local settings, and applies it **after a server restart**. It does not copy or upload the file.

Allowed locations include the application directory, configured `PANBANK_HOME`, and optionally paths in `PANBANK_DB_ALLOWED_DIRS` (`;` separated on Windows, `:` on Linux). Environment-variable settings can override portions of the saved configuration; avoid stale overrides. A missing selected database must be corrected instead of silently treating an empty database as your financial history.

### Backups

Before upgrades, back up **both the SQLite database and `panbank_settings.json`**. Use the application's SQLite backup function when possible, rather than copying an active database while writes may be occurring. Keep separate periodic copies; automated daily backup/retention is **not yet built in**. SQLite data and backup files are **not encrypted by PanBank**. Protect the host account, disks and backup locations.

## Data entry, searching and reporting

### Date and number formats

- **Web input/display:** dates in European format **`DD/MM/YYYY`**, e.g. `15/09/2026`; internally stored as ISO `YYYY-MM-DD` for correct database sorting.
- **Manual amounts:** dot-decimal entry, e.g. `1345.50`, with no thousands separator.
- **CSV monetary import:** Greek conventions, e.g. `1.345,50`; semicolon-separated files. Generic CSV imports use the destination register's exported database-field headers, while Income/Expense retain their specialized human-readable import templates.
- **Portfolio:** two decimal places for monetary/numeric displays and six decimal places for units (useful for fractional cryptocurrency holdings). Display precision is not the same as storage/calculation precision.

### Search and display limits

Income transactions, Expense transactions, Monthly snapshots, Investment transactions and Annual budgets have a selector with **Last 50 (default), Last 100, 1 month, 2 months, 3 months, Year, All**. Annual budget records are year-based rather than transaction-date based.

Unfiltered browsing is limited at the database level to avoid unnecessarily loading every record. **Search text and column-value filters scan the full register**, not only the first 50 or 100 shown. Within an Excel-style filter, selected values are OR-ed in a column and AND-ed across columns. Clicking column headings sorts results. For older versions of the filtering engine, the distinct-values chooser scans up to 10,000 rows; be mindful of that limit when working with very large databases.

### Editing and deletion

New/Edit opens a separate browser window, and saving refreshes the originating register and closes the editor window. Pop-up blockers may cause fallback to normal navigation. Multi-row deletion requires **two independent confirmations** and runs inside a single SQLite transaction; foreign-key constraints can stop the entire batch without partial deletion.

### Charts and PDF

Six chart choices are available, including **horizontal Budget vs Actual expense bars** to improve label readability; related PDF reports use landscape orientation where appropriate. PDF export is available across supported pages except **Data & Backups** and **Database Management**. Register PDF output follows the chosen displayed register view (e.g. **Last 50** versus **All**).

## Version history: v4.0–v4.4.1

This table tracks meaningful milestones; patch versions generally fix regressions introduced during rapid web migration. **Current behavior is described above.**

| Version | Changes |
|---|---|
| **v4.0** | Initial FastAPI/Jinja2/SQLite browser migration from desktop v3.2.x. Single-user login, Dashboard, registers, investment/debt workflows, Category income annual allocation, budgets, search, basic charts, SQLite backup. Early allocated Dashboard calculations were later superseded. |
| **v4.0.1** | Added **Settings → Database Management** to validate and select an existing SQLite database on the server, with restart-to-apply behavior and legacy selection persistence. |
| **v4.0.2** | Introduced elective annual `/12` allocation for expense child categories; added local setup and persistent `panbank_settings.json` with salted password hash and session secret, reducing the need for environment variables. |
| **v4.0.3** | Removed minimum password-length restriction, including support for short or empty passwords. Retained salted password hashing and confirmation. |
| **v4.1** | Added Excel-style multi-select column filters, double-confirmation atomic multi-record deletion, generic CSV imports across remaining registers, and three additional chart variants (six total). |
| **v4.2** | Made analytical `/12` allocation automatic for **all** expense child categories; removed their month/year allocation choice. Dashboard current-year period was restricted to completed calendar months. This release's allocation-in-Dashboard behavior was later corrected. |
| **v4.2.1** | Corrected Dashboard/template-variable compatibility and Windows launch-log handling. Fixed Windows Script Host launcher encoding and clarified stale-server process behavior. |
| **v4.3** | Restored actual transaction-month Dashboard cash-flow calculations; introduced record-range selectors, PDF export, debt repayment cash-flow integration, **Paid by other**, and repayment adjustments to remaining installments. |
| **v4.3.1** | Fixed selected-month and monthly-table expense reporting to use actual expenses, renamed footer to **SUMs (completed months)**, and replaced the lower reconciliation display with five completed-month average cards. |
| **v4.3.2** | Fixed PDF chart generation when a closing balance was missing, preserving missing values instead of silently inventing zero balances. |
| **v4.3.3** | Fixed blank PDF register exports caused by removing HTML forms containing data tables. |
| **v4.3.4** | Strengthened PDF table extraction for Income, Expense, Investment transactions, Annual budgets and other registers; restored Dashboard card labels in exported PDFs. Adopted as the stable PDF export baseline. |
| **v4.4** | European date entry/display; Windows launcher `/login`; full-register searching despite Last 50/100; new/edit pop-up windows; year dropdowns; portfolio decimal formatting; horizontal Budget vs Actual chart; income/expense Course summary cards. Added/registered the `eurodate` Jinja filter (both `panbank.py` and templates must match). |
| **v4.4.1** | Course UI refinement: displayed core equation, replaced six metric cards with two three-line cards, colored differences green when positive and red otherwise, and moved expense parent totals to a separate table. **No accounting formula change.** |

### Important historical changes to the accounting design

1. **v4.0–v4.2:** Analytical allocations were developed and initially mixed into some Dashboard calculations.
2. **v4.3–v4.3.1:** Dashboard was explicitly returned to **actual dated cash flows**, including personal debt repayments and investment cash flows, with a completed-month summary and separately computed averages.
3. **v4.4–v4.4.1:** Mainly usability, date formatting and Course presentation changes; the actual-cash-flow Dashboard interpretation remains authoritative.

## Migration and upgrade notes

1. **Stop PanBank completely** before copying a newer release, especially when using the hidden Windows launcher.
2. **Back up your active SQLite database and `panbank_settings.json`.** Confirm which database is selected in Database Management.
3. Replace `panbank.py`, `core.py` and the **complete `templates/` directory** as a matching set. Update `StartWinPanBank.vbs`, `requirements.txt` and any other distributed application files as needed.
4. Run `python -m pip install -r requirements.txt` (or the equivalent Python from your virtual environment) so newly introduced PDF and other dependencies are installed.
5. Restart and verify the version shown in the browser header.
6. Check Dashboard totals, Course summaries, PDF exports, and representative investment/debt movements against a known-good month.

Additive SQLite schema migrations may run on startup (for example, category allocation attributes and `debt_payments.paid_by_other`). Never experiment with migrations against your only database copy. Do not run the old desktop app and web app as concurrent writers on the same SQLite file stored on a network share.

## Limitations and validation

- **Single user:** no role-based access control or multi-user collaboration.
- **Manual finance tracking:** no direct bank feeds, automated transaction matching, automatic market-price updates, or exchange-rate conversion. Currency labels alone do not convert values; financial totals assume consistent comparable units.
- **Balance consistency:** missing/partial snapshots and transfers may distort estimated unregistered expenses. Treat negative or unusually large estimates as reconciliation signals to investigate, not as automatically verified spending.
- **Duplicate risk:** CSV imports and manually mirrored investment/debt records can double-count activity. Review imports and duplicate entries carefully.
- **Legacy desktop parity:** the original workbook-specific Excel importer, one-workbook all-table Excel exporter, and spreadsheet-style inline editing are not fully ported as such. CSV and PDF functionality are separate alternatives.
- **Backups/security:** no built-in scheduled backup policy or SQLite encryption. Restrict web access to localhost/private network, use a strong login password, and protect backups.
- **Testing:** automated regression tests accompanied the releases, but your actual Windows financial dataset remains the definitive reconciliation check. Test upgrades first on a backup copy.

---

**PanBank Web 4.4.1** is the current Version 4 baseline. Future 4.x patch releases should prioritize maintenance, reconciliation correctness, and data safety over altering the financial logic without explicit tests.
