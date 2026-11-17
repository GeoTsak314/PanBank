# PanBank Web v4.2 — Financial engine 
# (adapted from PanBank v3.2.17 by G. Tsakalos)
from __future__ import annotations
import calendar, csv, json, os, re, sqlite3
from datetime import date,datetime,timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
APP_DIR=Path(__file__).resolve().parent
DB_FILENAME='panbank.sqlite3'
# Database selection is recorded by the authenticated Database Management page.
# A chosen database is applied only after the server restarts.
CONFIG_FILE = APP_DIR / 'panbank_db_config.json'
SETTINGS_FILE = APP_DIR / 'panbank_settings.json'

def read_app_settings():
    if not SETTINGS_FILE.exists():return {}
    try:
        value=json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError('Settings must be a JSON object')
        return value
    except (OSError,ValueError) as exc:
        raise RuntimeError(f'Invalid settings file {SETTINGS_FILE}: {exc}') from exc


def configured_db_path():
    settings = read_app_settings()
    default = Path(os.environ.get('PANBANK_HOME') or settings.get('database_directory') or str(APP_DIR)).expanduser().resolve() / DB_FILENAME
    selected = settings.get('database_path')
    # Backward compatibility with Database Management in v4.0.1.
    if not selected and CONFIG_FILE.is_file():
        try:
            selected = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))['database_path']
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise RuntimeError(f'Invalid database selection file: {CONFIG_FILE}') from exc
    if selected:
        candidate = Path(selected).expanduser().resolve()
        if candidate.is_file():return candidate
        raise RuntimeError(f'Configured PanBank database missing: {candidate}. Restore it or correct the settings file.')
    return default

DB_PATH = configured_db_path()
DATA_DIR = DB_PATH.parent
def cents(value):
    """Parse ONLY Greek-formatted EUR amounts: 1.345,50 / 1345,50 / 1.345.

    Dot = grouping (exact groups of three); comma = decimal (one or two digits).
    Ungrouped integer amounts are allowed. Never guess US decimal formats.
    """
    import re
    original = value
    try:
        raw = str(value).strip()
        # No spaces, currency symbols, or mixed locales inside a monetary amount.
        if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,2})?', raw):
            raise ValueError('expected Greek numeric format')
        normalized = raw.replace('.', '').replace(',', '.')
        return int((Decimal(normalized) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Invalid Greek monetary amount: {original!r} (example: 1.345,50)')

def keyboard_cents(value):
    """Parse manual EUR input using the numeric keypad's dot as decimal separator.

    No thousands grouping or decimal commas are accepted in manual entry.
    CSV imports keep the existing Greek-formatted parser for compatibility.
    """
    import re
    original = value
    raw = str(value).strip()
    if not re.fullmatch(r'[+-]?\d+(?:\.\d{1,2})?', raw):
        raise ValueError(f'Invalid amount: {original!r}. Type 1345.50 (dot for decimal, no thousands separator).')
    try:
        return int((Decimal(raw) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Invalid monetary amount: {original!r}')

def keyboard_money_text(value):
    """Ungrouped dot-decimal value for edit controls, e.g. 1345.50."""
    return f'{Decimal(int(value or 0)) / 100:.2f}'

def money_text(value):
    """Format an integer-cent EUR value using Greek punctuation."""
    amount = Decimal(int(value or 0)) / 100
    return f'{amount:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

def eur(value):
    return money_text(value) + ' €'

def iso(value):
    if isinstance(value, datetime): return value.date().isoformat()
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, (int,float)):
        return (date(1899,12,30)+timedelta(days=float(value))).isoformat()
    s=str(value).strip()
    if not s: raise ValueError('Date cannot be blank')
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d.%m.%Y'):
        try: return datetime.strptime(s,fmt).date().isoformat()
        except ValueError: pass
    raise ValueError(f'Unknown date format: {s}')

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('income','expense')), name TEXT NOT NULL, parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT, active INTEGER NOT NULL DEFAULT 1, UNIQUE(kind,name));
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, institution TEXT DEFAULT '', account_number TEXT DEFAULT '', kind TEXT NOT NULL DEFAULT 'Bank', currency TEXT NOT NULL DEFAULT 'EUR', notes TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS currencies(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS payment_methods(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT DEFAULT 'Card', linked_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL, notes TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS incomes(id INTEGER PRIMARY KEY, tx_date TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0), category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT, provider TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, tx_date TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0), category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT, source_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL, payment_method_id INTEGER REFERENCES payment_methods(id) ON DELETE SET NULL, seller TEXT DEFAULT '', invoice_to TEXT DEFAULT '', status TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, snapshot_date TEXT NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT, amount INTEGER NOT NULL, notes TEXT DEFAULT '', UNIQUE(snapshot_date, account_id));
CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY, year INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('income','expense')), category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT, amount INTEGER NOT NULL DEFAULT 0, notes TEXT DEFAULT '', UNIQUE(year,kind,category_id));
CREATE TABLE IF NOT EXISTS investment_assets(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, asset_type TEXT DEFAULT 'Other', symbol TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS investments(id INTEGER PRIMARY KEY, tx_date TEXT NOT NULL, asset_id INTEGER NOT NULL REFERENCES investment_assets(id) ON DELETE RESTRICT, action TEXT NOT NULL CHECK(action IN ('Buy','Sell','Dividend','Income','Fee')), units TEXT NOT NULL DEFAULT '0', unit_price TEXT NOT NULL DEFAULT '0', fees INTEGER NOT NULL DEFAULT 0, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS valuations(id INTEGER PRIMARY KEY, value_date TEXT NOT NULL, asset_id INTEGER NOT NULL REFERENCES investment_assets(id) ON DELETE RESTRICT, unit_price TEXT NOT NULL, UNIQUE(value_date,asset_id));
CREATE TABLE IF NOT EXISTS debts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Tax', total INTEGER NOT NULL, installments INTEGER NOT NULL DEFAULT 1 CHECK(installments>0), first_due TEXT NOT NULL, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS debt_payments(id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL REFERENCES debts(id) ON DELETE CASCADE, paid_date TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TRIGGER IF NOT EXISTS currencies_prevent_delete BEFORE DELETE ON currencies
WHEN EXISTS(SELECT 1 FROM accounts WHERE currency=OLD.code COLLATE NOCASE)
BEGIN SELECT RAISE(ABORT,'Currency is in use by an account'); END;
CREATE TRIGGER IF NOT EXISTS currencies_prevent_code_change BEFORE UPDATE OF code ON currencies
WHEN NEW.code!=OLD.code AND EXISTS(SELECT 1 FROM accounts WHERE currency=OLD.code COLLATE NOCASE)
BEGIN SELECT RAISE(ABORT,'Currency code is in use by an account'); END;
CREATE INDEX IF NOT EXISTS income_date ON incomes(tx_date);
CREATE INDEX IF NOT EXISTS expense_date ON expenses(tx_date);
CREATE INDEX IF NOT EXISTS snapshot_date ON snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS investment_date ON investments(tx_date);
-- Enforce mandatory second-level expense categories for ALL records and import paths.
CREATE TRIGGER IF NOT EXISTS expense_leaf_insert BEFORE INSERT ON expenses
BEGIN
 SELECT CASE WHEN NEW.category_id IS NULL OR NOT EXISTS (
   SELECT 1 FROM categories c WHERE c.id=NEW.category_id AND c.kind='expense' AND c.parent_id IS NOT NULL
 ) THEN RAISE(ABORT,'An expense must use a level-2 expense subcategory') END;
END;
CREATE TRIGGER IF NOT EXISTS expense_leaf_update BEFORE UPDATE OF category_id ON expenses
BEGIN
 SELECT CASE WHEN NEW.category_id IS NULL OR NOT EXISTS (
   SELECT 1 FROM categories c WHERE c.id=NEW.category_id AND c.kind='expense' AND c.parent_id IS NOT NULL
 ) THEN RAISE(ABORT,'An expense must use a level-2 expense subcategory') END;
END;
CREATE TRIGGER IF NOT EXISTS expense_category_guard BEFORE UPDATE OF parent_id,kind ON categories
WHEN EXISTS (SELECT 1 FROM expenses e WHERE e.category_id=OLD.id)
BEGIN
 SELECT CASE WHEN NEW.parent_id IS NULL OR NEW.kind!='expense'
 THEN RAISE(ABORT,'Expense categories in use must stay level-2') END;
END;
CREATE TRIGGER IF NOT EXISTS expense_budget_leaf_insert BEFORE INSERT ON budgets
WHEN NEW.kind='expense'
BEGIN
 SELECT CASE WHEN NOT EXISTS (
   SELECT 1 FROM categories c WHERE c.id=NEW.category_id AND c.kind='expense' AND c.parent_id IS NOT NULL
 ) THEN RAISE(ABORT,'Expense budgets require a level-2 subcategory') END;
END;
CREATE TRIGGER IF NOT EXISTS expense_budget_leaf_update BEFORE UPDATE OF kind,category_id ON budgets
WHEN NEW.kind='expense'
BEGIN
 SELECT CASE WHEN NOT EXISTS (
   SELECT 1 FROM categories c WHERE c.id=NEW.category_id AND c.kind='expense' AND c.parent_id IS NOT NULL
 ) THEN RAISE(ABORT,'Expense budgets require a level-2 subcategory') END;
END;
'''

class Store:
    def __init__(self, path=DB_PATH):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON'); self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript(SCHEMA)
        # Add currencies to existing v3.2.2 databases without replacing any records.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO currencies(code,name) VALUES ('EUR','Euro')")
            for row in self.db.execute("SELECT DISTINCT currency FROM accounts WHERE currency IS NOT NULL AND TRIM(currency)<>''").fetchall():
                code=str(row[0]).strip().upper()
                if code:
                    self.db.execute("INSERT OR IGNORE INTO currencies(code,name) VALUES (?,?)",(code, {'USD':'US Dollar','GBP':'British Pound','CHF':'Swiss Franc','EUR':'Euro'}.get(code,code)))
    def query(self,sql,args=()): return self.db.execute(sql,args).fetchall()
    def scalar(self,sql,args=()): return self.db.execute(sql,args).fetchone()[0]
    def run(self,sql,args=()):
        with self.db: return self.db.execute(sql,args)
    def backup(self,path):
        with sqlite3.connect(path) as destination: self.db.backup(destination)
    def close(self): self.db.close()

MODELS = {
 'Income register':('incomes',[('tx_date','Date','date'),('amount','Amount (€)','money'),('category_id','Category','income_category'),('provider','Provider','text'),('notes','Notes','text')]),
 'Expense register':('expenses',[('tx_date','Date','date'),('amount','Amount (€)','money'),('category_id','Category','expense_category'),('source_account_id','Payment source','account'),('payment_method_id','Payment medium','payment'),('notes','Notes','text'),('seller','Seller','text'),('invoice_to','Invoice to','text'),('status','Status','text')]),
 'Monthly snapshots':('snapshots',[('snapshot_date','Snapshot date','date'),('account_id','Account','account'),('amount','Balance (€)','money'),('notes','Notes','text')]),
 'Accounts':('accounts',[('name','Account name','text'),('institution','Institution','text'),('account_number','IBAN / account no.','text'),('currency','Currency','currency_code'),('notes','Notes','text'),('active','Active','bool')]),
 'Currencies':('currencies',[('code','Currency code','text'),('name','Currency name','text'),('active','Active','bool')]),
 'Payment methods':('payment_methods',[('name','Method name','text'),('kind','Type','method_type'),('linked_account_id','Linked account','account'),('notes','Notes','text'),('active','Active','bool')]),
 'Investment assets':('investment_assets',[('name','Asset name','text'),('asset_type','Asset type','asset_type'),('symbol','Symbol','text'),('notes','Notes','text')]),
 'Investment transactions':('investments',[('tx_date','Date','date'),('asset_id','Asset','asset'),('action','Action','action'),('units','Units','decimal'),('unit_price','Unit price (€)','decimal'),('fees','Fees (€)','money'),('notes','Notes','text')]),
 'Investment valuations':('valuations',[('value_date','Date','date'),('asset_id','Asset','asset'),('unit_price','Unit price (€)','decimal')]),
 'Debts':('debts',[('name','Description','text'),('kind','Type','debt_type'),('total','Total owed (€)','money'),('installments','No. of installments','integer'),('first_due','First due date','date'),('notes','Notes','text')]),
 'Debt payments':('debt_payments',[('debt_id','Debt','debt'),('paid_date','Payment date','date'),('amount','Paid (€)','money'),('paid_by_other','Paid by other','bool'),('notes','Notes','text')]),
 'Categories':('categories',[('kind','Group type','category_kind'),('name','Category name','text'),('parent_id','Parent category','parent_category'),('active','Active','bool')]),
 'Annual budgets':('budgets',[('year','Year','year'),('kind','Group type','category_kind'),('category_id','Category','budget_category'),('amount','Annual planned (€)','money'),('notes','Notes','text')]),
}

LOOKUPS={'income_category':('categories',"kind='income' AND active=1"), 'expense_category':('categories',"kind='expense' AND active=1 AND parent_id IS NOT NULL"), 'any_category':('categories','active=1'), 'budget_category':('categories',"active=1 AND (kind='expense' AND parent_id IS NOT NULL OR kind='income' AND NOT EXISTS (SELECT 1 FROM categories child WHERE child.parent_id=categories.id))"), 'parent_category':('categories','active=1 AND parent_id IS NULL'), 'currency_code':('currencies','active=1'), 'account':('accounts','active=1'),'payment':('payment_methods','active=1'),'asset':('investment_assets','1=1'),'debt':('debts','1=1')}

OPTIONS={'category_kind':['income','expense'],'account_type':['Bank Account','Card','Cash','Crypto wallet'],'method_type':['Card','Inter-bank transfer','e-median','Physical'],'asset_type':['Crypto','Stocks','Materials','Real Estate','ETF','Bond','Fund'],'action':['Buy','Sell','Dividend','Income','Fee'],'debt_type':['Tax','Loan','Other']}

def display(store,val,typ):
    if val is None:return ''
    if typ=='money':return money_text(val)
    if typ in LOOKUPS:
        table=LOOKUPS[typ][0]
        key='code' if typ=='currency_code' else 'name'
        lookup_field='code' if typ=='currency_code' else 'id'
        result=store.query(f'SELECT {key} FROM {table} WHERE {lookup_field}=?',(val,))
        if result and typ in ('expense_category','any_category'):
            parents=store.query('SELECT p.name FROM categories c JOIN categories p ON p.id=c.parent_id WHERE c.id=?',(val,))
            if parents:return parents[0]['name']+' → '+result[0][0]
        return result[0][0] if result else f'#{val}'
    if typ=='bool':return 'Yes' if val else 'No'
    return str(val)

def validate_record(store,table,values,record_id=None):
    """Enforce leaf-only expense assignment without changing the v0.2 database schema."""
    if table=='currencies':
        code=str(values.get('code','')).strip().upper()
        if not code or len(code)!=3 or not code.isalpha():
            raise ValueError('Currency code must contain exactly three letters (e.g. EUR).')
        values['code']=code
    if table=='accounts':
        code=str(values.get('currency') or '').strip()
        if not store.query('SELECT 1 FROM currencies WHERE code=? COLLATE NOCASE',(code,)):
            raise ValueError('Select an existing currency from Sources → Currencies.')
    if table=='categories':
        kind=values.get('kind');parent=values.get('parent_id')
        if parent is not None:
            p=store.query('SELECT id,kind,parent_id FROM categories WHERE id=?',(parent,))
            if not p or p[0]['kind']!=kind or p[0]['parent_id'] is not None:
                raise ValueError('Subcategories must belong to a root category of the same type (two levels only).')
            if record_id is not None and parent==record_id:raise ValueError('A category cannot be its own parent.')
        if record_id is not None:
            existing=store.query('SELECT parent_id,kind FROM categories WHERE id=?',(record_id,))
            children=store.scalar('SELECT COUNT(*) FROM categories WHERE parent_id=?',(record_id,))
            if children and (parent is not None or kind!=existing[0]['kind']):
                raise ValueError('A root with subcategories cannot become a subcategory or change type.')
            if kind!=existing[0]['kind'] and store.scalar('SELECT COUNT(*) FROM expenses WHERE category_id=?',(record_id,)):
                raise ValueError('Cannot change type of a category used by expenses.')
    if table=='expenses':
        c=store.query('SELECT kind,parent_id FROM categories WHERE id=?',(values['category_id'],))
        if not c or c[0]['kind']!='expense' or c[0]['parent_id'] is None:
            raise ValueError('Expenses require a level-2 expense subcategory, not a level-1 root.')
    if table=='budgets':
        c=store.query('SELECT kind,parent_id FROM categories WHERE id=?',(values['category_id'],))
        if not c or c[0]['kind']!=values['kind']:
            raise ValueError('Budget category and budget type must match.')
        if values['kind']=='expense' and c[0]['parent_id'] is None:
            raise ValueError('Expense budgets must use level-2 subcategories; root totals are calculated.')
        if values['kind']=='income' and store.scalar('SELECT COUNT(*) FROM categories WHERE parent_id=?',(values['category_id'],)):
            raise ValueError('Income budgets must use terminal categories, not parent categories with children.')

TRANSACTION_CSV_HEADERS = {
    'incomes': ['Date', 'Amount', 'Group', 'Providers', 'Notes'],
    'expenses': ['Date', 'Amount', 'Group', 'Notes', 'PaymentSource', 'Median', 'Seller', 'InvoiceTo', 'Fail?'],
}

TRANSACTION_CSV_FIELDS = {
    'incomes': ['tx_date', 'amount', 'category_id', 'provider', 'notes'],
    'expenses': ['tx_date', 'amount', 'category_id', 'notes', 'source_account_id', 'payment_method_id', 'seller', 'invoice_to', 'status'],
}

def csv_lookup(store, label, kind, line):
    # Resolve the template name or ID.
    label = str(label or '').strip()
    if not label:
        return None
    table, condition = LOOKUPS[kind]
    rows = store.query(f'SELECT id, name FROM {table} WHERE {condition}')
    if label.isdecimal():
        matches = [r for r in rows if r['id'] == int(label)]
        if matches:
            return matches[0]['id']
    # Root → Child, Root > Child, and simple child names are supported.
    path = label.replace('->', '→').replace('>', '→')
    segments = [x.strip() for x in path.split('→')]
    matches = [r for r in rows if r['name'].casefold() == segments[-1].casefold()]
    if len(segments) > 1 and kind in ('income_category', 'expense_category'):
        matches = [r for r in matches if store.query("SELECT 1 FROM categories child JOIN categories root ON root.id=child.parent_id WHERE child.id=? AND root.name=? COLLATE NOCASE", (r['id'],segments[-2]))]
    if len(matches) == 1:
        return matches[0]['id']
    if not matches:
        raise ValueError(f'CSV line {line}: {kind} {label!r} not found. Create it in PanBank first.')
    raise ValueError(f'CSV line {line}: ambiguous {kind} {label!r}; use Root → Subcategory or numeric ID.')

def read_transaction_csv(store, path, table_name):
    # Parse before writing, skipping blank rows.
    expected = TRANSACTION_CSV_HEADERS[table_name]
    fields = TRANSACTION_CSV_FIELDS[table_name]
    output = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f, delimiter=';')
        header = next(reader, None)
        if header is None or [x.strip() for x in header] != expected:
            raise ValueError('Unexpected CSV template. Expected semicolon-separated columns: ' + ';'.join(expected))
        for line, cells in enumerate(reader, 2):
            if not any(str(x).strip() for x in cells):
                continue
            if len(cells) != len(expected):
                raise ValueError(f'CSV line {line}: expected {len(expected)} columns; found {len(cells)}. Check delimiters and quotes.')
            row = dict(zip(expected,(x.strip() for x in cells)))
            if not row['Date'] or not row['Amount']:
                raise ValueError(f'CSV line {line}: Date and Amount are mandatory.')
            values = {'tx_date':iso(row['Date']), 'amount':cents(row['Amount']), 'notes':row['Notes']}
            if table_name == 'incomes':
                values.update(category_id=csv_lookup(store,row['Group'],'income_category',line),provider=row['Providers'])
            else:
                values.update(category_id=csv_lookup(store,row['Group'],'expense_category',line),
                    source_account_id=csv_lookup(store,row['PaymentSource'],'account',line),
                    payment_method_id=csv_lookup(store,row['Median'],'payment',line),
                    seller=row['Seller'],invoice_to=row['InvoiceTo'],status=row['Fail?'])
            validate_record(store,table_name,values)
            output.append(tuple(values[x] for x in fields))
    return fields,output

def month_bounds(year,month):
    start=f'{year:04d}-{month:02d}-01';end=f'{year:04d}-{month:02d}-{calendar.monthrange(year,month)[1]:02d}'
    return start,end

def snapshot_boundary_dates(year, month):
    "Return first-day opening and next-month closing dates (YYYY-MM-DD)."
    opening = date(year, month, 1)
    closing = add_month(opening, 1)
    return opening.isoformat(), closing.isoformat()

def snapshot_accounts_on(store, snapshot_date):
    "Return only snapshots recorded on this exact boundary date."
    return store.query('''SELECT s.account_id, s.amount, s.snapshot_date, a.name
        FROM snapshots s JOIN accounts a ON a.id = s.account_id
        WHERE s.snapshot_date = ? ORDER BY a.name COLLATE NOCASE''',
        (snapshot_date,))

def investment_cashflows(store, first, last):
    """Derive EUR cash movements from investment records; never post duplicates.

    Buy: cash outflow (including fees); Sell: cash inflow net of fees;
    Dividend/Income: cash inflow net of fees; Fee: cash outflow.
    These are GROSS cash movements, not realized trading gains or budget categories.
    """
    rows=store.query("""SELECT i.id, i.tx_date, i.action, i.units, i.unit_price,
                      i.fees, i.notes, a.name AS asset
                      FROM investments i JOIN investment_assets a ON a.id=i.asset_id
                      WHERE i.tx_date BETWEEN ? AND ? ORDER BY i.tx_date DESC,i.id DESC""",(first,last))
    income=0; expenses=0; entries=[]
    for row in rows:
        units=Decimal(row['units']); price=Decimal(row['unit_price'])
        fees=int(row['fees'] or 0)
        gross=int((units*price*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        action=row['action']
        if action=='Buy':
            kind='expenses'; amount=gross+fees
        elif action in ('Sell','Dividend','Income'):
            kind='income'; amount=gross-fees
        elif action=='Fee':
            kind='expenses'; amount=gross+fees
        else:
            continue
        if amount < 0:
            raise ValueError(f'Investment #{row["id"]}: negative net cash flow ({action}); review fees.')
        if kind=='income':income+=amount
        else:expenses+=amount
        entries.append(dict(id=row['id'],date=row['tx_date'],action=action,asset=row['asset'],
                            amount=amount,kind=kind,notes=row['notes'] or ''))
    return dict(income=income,expenses=expenses,records=entries)

def add_month(d,delta):
    m=(d.month-1+delta);y=d.year+m//12;m=m%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))

def debt_schedule(store):
    '''Apply repayments to oldest outstanding scheduled installments, in cents.

    Payments made on or before an installment date are allocated to the earliest
    outstanding installment; late repayments clear arrears first. The due date
    remains the contractual due date, not the date cash was paid.
    '''
    entries=[]
    for d in store.query('SELECT * FROM debts ORDER BY first_due,id'):
        count=max(1,d['installments'])
        each,extra=divmod(d['total'],count)
        dues=[each+(1 if i<extra else 0) for i in range(count)]
        paid=sum(r['amount'] for r in store.query('SELECT amount FROM debt_payments WHERE debt_id=?',(d['id'],)))
        outstanding=max(0,d['total']-paid)
        remaining_paid=paid
        for i,scheduled in enumerate(dues):
            applied=min(scheduled,remaining_paid)
            remaining_paid-=applied
            outstanding_installment=scheduled-applied
            entries.append((d['name'],d['kind'],add_month(date.fromisoformat(d['first_due']),i).isoformat(),
                            outstanding_installment,outstanding))
    return entries

def debt_payment_cashflows(store,first,last):
    '''Cash paid personally only; payments by third parties are excluded.''' 
    rows=store.query('''SELECT p.id,p.paid_date,p.amount,p.notes,d.name AS debt_name
        FROM debt_payments p JOIN debts d ON d.id=p.debt_id
        WHERE p.paid_date BETWEEN ? AND ? AND COALESCE(p.paid_by_other,0)=0
        ORDER BY p.paid_date DESC,p.id DESC''',(first,last))
    return dict(expenses=sum(r['amount'] for r in rows),records=[
        dict(id=r['id'],date=r['paid_date'],action='Payment',asset=r['debt_name'],amount=r['amount'],
             kind='expenses',notes=r['notes'] or '') for r in rows])

def portfolio(store):
    """FIFO lots per asset, fees included in lot costs, EUR values; sales above holdings flagged."""
    result=[]
    for asset in store.query('SELECT * FROM investment_assets ORDER BY name'):
        lots=[];realized=Decimal('0');distributions=Decimal('0');errors=[]
        trades=store.query('SELECT * FROM investments WHERE asset_id=? ORDER BY tx_date,id',(asset['id'],))
        for t in trades:
            units=Decimal(t['units']);price=Decimal(t['unit_price']);fees=Decimal(t['fees'])/100
            if t['action']=='Buy':
                if units<=0:errors.append(f'Buy #{t["id"]}: units must be positive');continue
                lots.append([units,units*price+fees])
            elif t['action']=='Sell':
                if units<=0:errors.append(f'Sell #{t["id"]}: units must be positive');continue
                if units>sum((l[0] for l in lots),Decimal(0)):
                    errors.append(f'Sell #{t["id"]}: insufficient units');continue
                left=units;cost=Decimal(0)
                while left>0:
                    u,v=lots[0];used=min(left,u);portion=v*used/u;cost+=portion;left-=used
                    if used==u:lots.pop(0)
                    else:lots[0]=[u-used,v-portion]
                realized+=units*price-fees-cost
            elif t['action'] in ('Dividend','Income'):distributions+=units*price-fees
            elif t['action']=='Fee':realized-=fees+units*price
        units=sum((l[0] for l in lots),Decimal(0));cost=sum((l[1] for l in lots),Decimal(0))
        v=store.query('SELECT unit_price,value_date FROM valuations WHERE asset_id=? ORDER BY value_date DESC LIMIT 1',(asset['id'],))
        current=units*Decimal(v[0]['unit_price']) if v else None
        result.append(dict(name=asset['name'],units=units,cost=cost,realized=realized,distributions=distributions,value=current,unrealized=current-cost if current is not None else None,errors=errors))
    return result

# Web v4.0 data migration, additive and safe for the v3.2.17 SQLite schema.
# An income category may be marked as an annual item, allocated across Jan-Dec.
def migrate_web(store):
    cols={r['name'] for r in store.query('PRAGMA table_info(categories)')}
    if 'income_allocation' not in cols:
        store.run("ALTER TABLE categories ADD COLUMN income_allocation TEXT NOT NULL DEFAULT 'normal'")
    if 'expense_allocation' not in cols:
        store.run("ALTER TABLE categories ADD COLUMN expense_allocation TEXT NOT NULL DEFAULT 'normal'")
    store.run('''CREATE TRIGGER IF NOT EXISTS expense_allocation_check_insert
        BEFORE INSERT ON categories WHEN NEW.expense_allocation NOT IN ('normal','annual_12')
        OR (NEW.expense_allocation='annual_12' AND (NEW.kind!='expense' OR NEW.parent_id IS NULL))
        BEGIN SELECT RAISE(ABORT,'Only expense subcategories can be annualized'); END''')
    store.run('''CREATE TRIGGER IF NOT EXISTS expense_allocation_check_update
        BEFORE UPDATE OF expense_allocation,kind,parent_id ON categories
        WHEN NEW.expense_allocation NOT IN ('normal','annual_12')
        OR (NEW.expense_allocation='annual_12' AND (NEW.kind!='expense' OR NEW.parent_id IS NULL))
        BEGIN SELECT RAISE(ABORT,'Only expense subcategories can be annualized'); END''')
    payment_cols={r['name'] for r in store.query('PRAGMA table_info(debt_payments)')}
    if 'paid_by_other' not in payment_cols:
        store.run("ALTER TABLE debt_payments ADD COLUMN paid_by_other INTEGER NOT NULL DEFAULT 0 CHECK(paid_by_other IN (0,1))")
        store.run('''CREATE TRIGGER IF NOT EXISTS income_allocation_check_insert
        BEFORE INSERT ON categories WHEN NEW.income_allocation NOT IN ('normal','annual_12')
        BEGIN SELECT RAISE(ABORT,'Invalid income allocation'); END''')
    store.run('''CREATE TRIGGER IF NOT EXISTS income_allocation_check_update
        BEFORE UPDATE OF income_allocation ON categories WHEN NEW.income_allocation NOT IN ('normal','annual_12')
        BEGIN SELECT RAISE(ABORT,'Invalid income allocation'); END''')

def allocated_yearly_cents(store,year):
    """Total annual income to spread. Category set per income row; never modify original."""
    rows=store.query('''SELECT i.amount,i.category_id FROM incomes i JOIN categories c ON c.id=i.category_id
       WHERE i.tx_date>=? AND i.tx_date<? AND c.income_allocation='annual_12' ''',
       (f'{year}-01-01',f'{year+1}-01-01'))
    return sum(r['amount'] for r in rows)

def allocated_yearly_expense_cents(store, year):
    """All expense subcategory transactions for the year, irrespective of legacy flags.

    Expense parent groups contain aggregates only and are never counted directly.
    """
    return store.scalar('''SELECT COALESCE(SUM(e.amount),0) FROM expenses e
        JOIN categories c ON c.id=e.category_id WHERE e.tx_date>=? AND e.tx_date<?
        AND c.kind='expense' AND c.parent_id IS NOT NULL ''',
        (f'{year}-01-01',f'{year+1}-01-01'))

def spread_month(total,month):
    """Integer cents allocation; first remainder months receive a cent. All 12 sum exactly."""
    q,r=divmod(total,12)
    return q+(1 if month<=r else 0)

def get_monthly(store,year,month):
    '''Actual dated cash flows; optional allocation is REPORTING ONLY, not reconciliation.''' 
    first,last=month_bounds(year,month)
    op,cl=snapshot_boundary_dates(year,month)
    open_rows=snapshot_accounts_on(store,op);close_rows=snapshot_accounts_on(store,cl)
    opening=sum(x['amount'] for x in open_rows);closing=sum(x['amount'] for x in close_rows)
    actual_income=store.scalar('SELECT COALESCE(SUM(amount),0) FROM incomes WHERE tx_date BETWEEN ? AND ?',(first,last))
    actual_exp=store.scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE tx_date BETWEEN ? AND ?',(first,last))
    inv=investment_cashflows(store,first,last)
    debt=debt_payment_cashflows(store,first,last)
    total_income=actual_income+inv['income']
    total_exp=actual_exp+inv['expenses']+debt['expenses']
    # Retain analytical allocation figures for reports, without modifying cash totals.
    annual_income=spread_month(allocated_yearly_cents(store,year),month)
    annual_exp=spread_month(allocated_yearly_expense_cents(store,year),month)
    return dict(opening=opening,closing=closing,income=total_income,expenses=total_exp,
       direct_income=actual_income,actual_cash_income=total_income,
       normal_income=actual_income,allocated_income=annual_income,
       investment_income=inv['income'],direct_expenses=actual_exp,
       normal_expenses=actual_exp,allocated_expenses=annual_exp,
       actual_cash_expenses=total_exp,investment_expenses=inv['expenses'],
       debt_expenses=debt['expenses'],
       unregistered=opening-closing+total_income-total_exp,
       opening_n=len(open_rows),closing_n=len(close_rows),
       opening_date=op,closing_date=cl,opening_accounts={r['account_id'] for r in open_rows},
       closing_accounts={r['account_id'] for r in close_rows})

def year_reconciliation(store,year,as_of=None):
    """Year-to-date reconciled between Jan 1 and latest available first-of-month <= Jan 1 next year.
    No misleading sum of monthly missing-boundary results.
    """
    as_of=as_of or date.today()
    start=f'{year}-01-01';end=min(f'{year+1}-01-01',as_of.replace(day=1).isoformat())
    dates=[r['snapshot_date'] for r in store.query('''SELECT DISTINCT snapshot_date FROM snapshots
       WHERE snapshot_date>? AND snapshot_date<=? AND substr(snapshot_date,9,2)='01'
       ORDER BY snapshot_date DESC''',(start,end))]
    opening=snapshot_accounts_on(store,start)
    if not opening or not dates:
        return dict(complete=False,reason='Missing Jan 1 opening or completed-month closing snapshot', start=start,end=None)
    boundary=dates[0]; closing=snapshot_accounts_on(store,boundary)
    if not closing:
        return dict(complete=False,reason='Closing snapshot missing',start=start,end=boundary)
    enddate=date.fromisoformat(boundary)-timedelta(days=1)
    income=expenses=0
    for m in range(1,enddate.month+1):
        data=get_monthly(store,year,m)
        income+=data['income'];expenses+=data['expenses']
    o=sum(r['amount'] for r in opening);c=sum(r['amount'] for r in closing)
    ids={r['account_id'] for r in opening}|{r['account_id'] for r in closing}
    missing_open=ids-{r['account_id'] for r in opening}
    missing_close=ids-{r['account_id'] for r in closing}
    return dict(complete=not(missing_open or missing_close), start=start,end=boundary,
      through=enddate.isoformat(), opening=o,closing=c,income=income,expenses=expenses,
      unregistered=o-c+income-expenses,missing_open=sorted(missing_open),missing_close=sorted(missing_close))

def budget_rows(store,year):
    result=[]
    for kind,table in [('income','incomes'),('expense','expenses')]:
        items=store.query('''SELECT c.id,c.name,c.parent_id,p.name parent,b.amount planned
           FROM categories c LEFT JOIN categories p ON p.id=c.parent_id
           LEFT JOIN budgets b ON b.category_id=c.id AND b.kind=? AND b.year=?
           WHERE c.kind=? AND (b.id IS NOT NULL OR EXISTS(SELECT 1 FROM '''+table+''' t
             WHERE t.category_id=c.id AND t.tx_date>=? AND t.tx_date<?))
           ORDER BY COALESCE(p.name,c.name) COLLATE NOCASE,c.name COLLATE NOCASE''',
           (kind,year,kind,f'{year}-01-01',f'{year+1}-01-01'))
        for r in items:
            actual=store.scalar(f'SELECT COALESCE(SUM(amount),0) FROM {table} WHERE category_id=? AND tx_date>=? AND tx_date<?',
              (r['id'],f'{year}-01-01',f'{year+1}-01-01'))
            planned=r['planned'] or 0
            # Per user's explicit sign convention: income actual-planned, expense planned-actual.
            difference=(actual-planned if kind=='income' else planned-actual)
            result.append(dict(kind=kind,name=(r['parent']+' → ' if r['parent'] else '')+r['name'],planned=planned,actual=actual,difference=difference))
    roots=store.query("SELECT id,name FROM categories WHERE kind='expense' AND parent_id IS NULL ORDER BY name")
    for root in roots:
        ch=store.query('SELECT id FROM categories WHERE parent_id=?',(root['id'],))
        if not ch:continue
        ids=[r['id'] for r in ch];marks=','.join('?' for _ in ids)
        planned=store.scalar(f'SELECT COALESCE(SUM(amount),0) FROM budgets WHERE year=? AND kind=? AND category_id IN ({marks})',(year,'expense',*ids))
        actual=store.scalar(f'SELECT COALESCE(SUM(amount),0) FROM expenses WHERE category_id IN ({marks}) AND tx_date>=? AND tx_date<?',(*ids,f'{year}-01-01',f'{year+1}-01-01'))
        result.append(dict(kind='expense total',name=root['name'],planned=planned,actual=actual,difference=planned-actual))
    return result
