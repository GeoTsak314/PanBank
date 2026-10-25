# PanBank v3.1 = local, single-user, snapshot-based personal financial planner 
# by G. Tsakalos 


from __future__ import annotations
import calendar
import csv
import json
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QDateEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QTabWidget, QMessageBox,
    QFileDialog, QDialog, QDialogButtonBox, QScrollArea, QHeaderView, QGroupBox,
    QCheckBox, QInputDialog, QAbstractItemView, QMenu, QToolButton, QTextBrowser)
from PySide6.QtCore import QDate


# Portable default: PanBank creates its SQLite file next to this Python script.
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / 'panbank_settings.json'
DB_FILENAME = 'panbank.sqlite3'


def configured_data_dir():
    # An explicit environment variable is useful for testing and automation.
    override = os.environ.get('PANBANK_HOME')
    if override:
        return Path(override).expanduser().resolve()
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            directory = data.get('database_directory')
            if isinstance(directory, str) and directory.strip():
                return Path(directory).expanduser().resolve()
        except (OSError, ValueError, TypeError):
            pass
    return APP_DIR

def save_data_dir(directory):
    directory = Path(directory).expanduser().resolve()
    if directory == APP_DIR:
        # No config file means the portable, application-folder default.
        CONFIG_PATH.unlink(missing_ok=True)
    else:
        temp = CONFIG_PATH.with_suffix('.json.tmp')
        try:
            temp.write_text(json.dumps({'database_directory': str(directory)}, indent=2), encoding='utf-8')
            temp.replace(CONFIG_PATH)
        finally:
            temp.unlink(missing_ok=True)

DATA_DIR = configured_data_dir()
DB_PATH = DATA_DIR / DB_FILENAME

# Integer cents for EUR monetary values; decimal text for fractional investment units/prices.
def cents(value):
    try:
        return int((Decimal(str(value).strip().replace(',', '.'))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError(f'Invalid monetary amount: {value!r}')

def eur(value):
    return f'{(Decimal(int(value or 0))/100):,.2f} €'

def money_text(value):
    return str((Decimal(int(value or 0))/100).quantize(Decimal('0.01')))

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
    def query(self,sql,args=()): return self.db.execute(sql,args).fetchall()
    def scalar(self,sql,args=()): return self.db.execute(sql,args).fetchone()[0]
    def run(self,sql,args=()):
        with self.db: return self.db.execute(sql,args)
    def backup(self,path):
        with sqlite3.connect(path) as destination: self.db.backup(destination)
    def close(self): self.db.close()

# Data-driven editors; every saved value uses parameterized queries.
MODELS = {
 'Income register':('incomes',[('tx_date','Date','date'),('amount','Amount (€)','money'),('category_id','Category','income_category'),('provider','Provider','text'),('notes','Notes','text')]),
 'Expense register':('expenses',[('tx_date','Date','date'),('amount','Amount (€)','money'),('category_id','Category','expense_category'),('source_account_id','Payment source','account'),('payment_method_id','Payment medium','payment'),('seller','Seller','text'),('invoice_to','Invoice to','text'),('status','Status','text'),('notes','Notes','text')]),
 'Monthly snapshots':('snapshots',[('snapshot_date','Snapshot date','date'),('account_id','Account','account'),('amount','Balance (€)','money'),('notes','Notes','text')]),
 'Accounts':('accounts',[('name','Account name','text'),('institution','Institution','text'),('account_number','IBAN / account no.','text'),('kind','Type','account_type'),('currency','Currency','text'),('notes','Notes','text'),('active','Active','bool')]),
 'Payment methods':('payment_methods',[('name','Method name','text'),('kind','Type','method_type'),('linked_account_id','Linked account','account'),('notes','Notes','text'),('active','Active','bool')]),
 'Investment assets':('investment_assets',[('name','Asset name','text'),('asset_type','Asset type','asset_type'),('symbol','Symbol','text'),('notes','Notes','text')]),
 'Investment transactions':('investments',[('tx_date','Date','date'),('asset_id','Asset','asset'),('action','Action','action'),('units','Units','decimal'),('unit_price','Unit price (€)','decimal'),('fees','Fees (€)','money'),('notes','Notes','text')]),
 'Investment valuations':('valuations',[('value_date','Date','date'),('asset_id','Asset','asset'),('unit_price','Unit price (€)','decimal')]),
 'Debts':('debts',[('name','Description','text'),('kind','Type','debt_type'),('total','Total owed (€)','money'),('installments','No. of installments','integer'),('first_due','First due date','date'),('notes','Notes','text')]),
 'Debt payments':('debt_payments',[('debt_id','Debt','debt'),('paid_date','Payment date','date'),('amount','Paid (€)','money'),('notes','Notes','text')]),
 'Categories':('categories',[('kind','Group type','category_kind'),('name','Category name','text'),('parent_id','Parent category','any_category'),('active','Active','bool')]),
 'Annual budgets':('budgets',[('year','Year','year'),('kind','Group type','category_kind'),('category_id','Category','any_category'),('amount','Annual planned (€)','money'),('notes','Notes','text')]),
}
LOOKUPS={'income_category':('categories',"kind='income' AND active=1"), 'expense_category':('categories',"kind='expense' AND active=1 AND parent_id IS NOT NULL"), 'any_category':('categories','active=1'), 'account':('accounts','active=1'),'payment':('payment_methods','active=1'),'asset':('investment_assets','1=1'),'debt':('debts','1=1')}
OPTIONS={'category_kind':['income','expense'],'account_type':['Bank','Cash','Wallet','Brokerage','Other'],'method_type':['Debit card','Credit card','PayPal','Prepaid card','Cash','Other'],'asset_type':['Crypto','Stock','ETF','Bond','Fund','Other'],'action':['Buy','Sell','Dividend','Income','Fee'],'debt_type':['Tax','Loan','Other']}

def display(store,val,typ):
    if val is None:return ''
    if typ=='money':return money_text(val)
    if typ in LOOKUPS:
        table=LOOKUPS[typ][0]
        key='name'
        result=store.query(f'SELECT {key} FROM {table} WHERE id=?',(val,))
        if result and typ in ('expense_category','any_category'):
            parents=store.query('SELECT p.name FROM categories c JOIN categories p ON p.id=c.parent_id WHERE c.id=?',(val,))
            if parents:return parents[0]['name']+' → '+result[0][0]
        return result[0][0] if result else f'#{val}'
    if typ=='bool':return 'Yes' if val else 'No'
    return str(val)

def validate_record(store,table,values,record_id=None):
    """Enforce leaf-only expense assignment without changing the v0.2 database schema."""
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
            raise ValueError('Expense budgets must be entered for level-2 subcategories; root totals are calculated.')

class RecordDialog(QDialog):
    def __init__(self,store,title,model,existing=None,parent=None):
        super().__init__(parent);self.store=store;self.model=model;self.fields={};self.setWindowTitle(title);self.resize(480,360)
        outer=QVBoxLayout(self); scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        container=QWidget(); form=QFormLayout(container);scroll.setWidget(container)
        for col,label,typ in model:
            val=existing[col] if existing is not None else None
            if typ in LOOKUPS or typ in OPTIONS:
                w=QComboBox();
                if typ in LOOKUPS:
                    table,condition=LOOKUPS[typ]; w.addItem('(none)',None)
                    rows=store.query(f'SELECT id,name FROM {table} WHERE {condition} ORDER BY name')
                    for r in rows:
                        label=r['name']
                        if typ in ('expense_category','any_category'):
                            path=store.query('SELECT p.name FROM categories c JOIN categories p ON p.id=c.parent_id WHERE c.id=?',(r['id'],))
                            if path:label=path[0]['name']+' → '+label
                        w.addItem(label,r['id'])
                    if val is not None and w.findData(val)<0:
                        rr=store.query(f'SELECT name FROM {table} WHERE id=?',(val,))
                        if rr:w.addItem(rr[0][0]+' (inactive)',val)
                else:
                    for v in OPTIONS[typ]: w.addItem(v,v)
                if val is not None:
                    i=w.findData(val)
                    if i>=0:w.setCurrentIndex(i)
            elif typ=='bool': w=QCheckBox();w.setChecked(bool(val) if val is not None else True)
            elif typ in ('date',):
                w=QDateEdit();w.setCalendarPopup(True);w.setDisplayFormat('yyyy-MM-dd')
                try:w.setDate(QDate.fromString(str(val),'yyyy-MM-dd') if val else QDate.currentDate())
                except Exception: w.setDate(QDate.currentDate())
            elif typ in ('year','integer'):w=QSpinBox();w.setRange(1,9999 if typ=='year' else 1000000);w.setValue(int(val) if val is not None else date.today().year if typ=='year' else 1)
            else:
                w=QLineEdit();w.setText(money_text(val) if typ=='money' and val is not None else str(val) if val is not None else '');
                if typ=='decimal':w.setPlaceholderText('e.g. 0.00000125')
                if typ=='money':w.setPlaceholderText('e.g. 1250.50')
            self.fields[col]=(w,typ);form.addRow(label,w)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
    def values(self):
        out={}
        for col,(w,typ) in self.fields.items():
            if typ in LOOKUPS or typ in OPTIONS: val=w.currentData()
            elif typ=='bool':val=int(w.isChecked())
            elif typ=='date':val=w.date().toString('yyyy-MM-dd')
            elif typ in ('year','integer'):val=w.value()
            elif typ=='money':val=cents(w.text() or 0)
            else:val=w.text().strip()
            if typ=='decimal':
                try:val=str(Decimal(val or 0))
                except InvalidOperation:raise ValueError(f'{col}: enter a decimal number')
            if col in ('name','tx_date','snapshot_date','first_due','paid_date') and not val: raise ValueError(f'{col} is required')
            if col in ('account_id','category_id','asset_id','debt_id') and val is None:raise ValueError(f'Please select {col}')
            out[col]=val
        return out

class CrudPage(QWidget):
    def __init__(self,store,model_name,on_change):
        super().__init__();self.store=store;self.name=model_name;self.table_name,self.model=MODELS[model_name];self.on_change=on_change;self.rows=[]
        layout=QVBoxLayout(self)
        bar=QHBoxLayout(); layout.addLayout(bar)
        for text,action in [('Add',self.add),('Edit selected',self.edit),('Delete selected',self.remove),('Filter',self.open_filter),('Refresh',self.refresh),('Export CSV',self.export_csv),('Import CSV',self.import_csv)]:
            b=QPushButton(text);b.clicked.connect(action);bar.addWidget(b)
        bar.addStretch()
        self.active_filter={}
        self.filter_label=QLabel('');layout.addWidget(self.filter_label)
        self.table=QTableWidget();self.table.setAlternatingRowColors(True);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnCount(1+len(self.model));self.table.setHorizontalHeaderLabels(['ID']+[x[1] for x in self.model]);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.edit);layout.addWidget(self.table)
        self.count=QLabel();layout.addWidget(self.count);self.refresh()
    def open_filter(self):
        dlg=QDialog(self);dlg.setWindowTitle('Filter — '+self.name)
        form=QFormLayout(dlg)
        field=QComboBox();field.addItem('Any field','*')
        for col,label,typ in self.model:field.addItem(label,col)
        query=QLineEdit(self.active_filter.get('term',''));query.setPlaceholderText('Case-insensitive text or amount')
        if self.active_filter.get('field'):
            ix=field.findData(self.active_filter['field']);field.setCurrentIndex(max(0,ix))
        form.addRow('Search field',field);form.addRow('Contains',query)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Reset|QDialogButtonBox.StandardButton.Cancel)
        def apply():self.active_filter={'field':field.currentData(),'term':query.text().strip()};dlg.accept()
        def clear():self.active_filter={};dlg.accept()
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(clear)
        buttons.rejected.connect(dlg.reject);form.addRow(buttons)
        if dlg.exec()==QDialog.DialogCode.Accepted:self.refresh()
    def refresh(self):
        cols=','.join(['id']+[col for col,_,_ in self.model])
        dates={'incomes':'tx_date','expenses':'tx_date','snapshots':'snapshot_date','investments':'tx_date','valuations':'value_date','debts':'first_due','debt_payments':'paid_date'}
        order=(dates[self.table_name]+ ' DESC, id DESC') if self.table_name in dates else ('year DESC, id DESC' if self.table_name=='budgets' else 'id DESC')
        full=self.store.query(f'SELECT {cols} FROM {self.table_name} ORDER BY {order} LIMIT 10000')
        term=self.active_filter.get('term','').casefold();col=self.active_filter.get('field','*')
        def match(r):
            if not term:return True
            candidates=[('id','ID','integer')]+self.model if col=='*' else [(c,l,t) for c,l,t in self.model if c==col]
            return any(term in str(display(self.store,r[c],typ) if c!='id' else r[c]).casefold() for c,_,typ in candidates)
        self.rows=[r for r in full if match(r)]
        self.table.setRowCount(len(self.rows))
        for i,r in enumerate(self.rows):
            for j,(field,_,typ) in enumerate([('id','ID','integer')]+self.model):
                cell=QTableWidgetItem(display(self.store,r[field],typ) if field!='id' else str(r['id']))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable);self.table.setItem(i,j,cell)
        self.filter_label.setText(f'Active filter: {term}' if term else '')
        self.count.setText(f'{len(self.rows)} of {len(full)} records shown (maximum 10,000 scanned)')
    def selected(self):
        i=self.table.currentRow();return self.rows[i] if 0<=i<len(self.rows) else None
    def add(self):self.open_editor(None)
    def edit(self,*_):
        r=self.selected()
        if r:self.open_editor(r)
    def open_editor(self,r):
        dlg=RecordDialog(self.store,('Edit' if r else 'New')+' — '+self.name,self.model,r,self)
        if dlg.exec()!=QDialog.DialogCode.Accepted:return
        try:
            d=dlg.values()
            validate_record(self.store,self.table_name,d,r['id'] if r else None)
            cols=list(d);q=','.join('?' for _ in cols)
            if r: self.store.run(f"UPDATE {self.table_name} SET "+','.join(f'{x}=?' for x in cols)+' WHERE id=?',tuple(d.values())+(r['id'],))
            else:self.store.run(f"INSERT INTO {self.table_name} ({','.join(cols)}) VALUES ({q})",tuple(d.values()))
            self.refresh();self.on_change()
        except (sqlite3.Error,ValueError) as e: QMessageBox.warning(self,'Cannot save',str(e))
    def remove(self):
        r=self.selected()
        if r and QMessageBox.question(self,'Confirm deletion',f'Delete {self.name} record #{r["id"]}?')==QMessageBox.StandardButton.Yes:
            try:self.store.run(f'DELETE FROM {self.table_name} WHERE id=?',(r['id'],));self.refresh();self.on_change()
            except sqlite3.Error as e: QMessageBox.warning(self,'Cannot delete',str(e)+'\nThis record may be referenced by another record.')
    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,'Export CSV',self.table_name+'.csv','CSV (*.csv)')
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                cols=[col for col,_,_ in self.model];wr=csv.writer(f);wr.writerow(cols)
                for r in self.store.query(f"SELECT {','.join(cols)} FROM {self.table_name} ORDER BY id"):
                    wr.writerow([money_text(r[c]) if typ=='money' else r[c] for c,_,typ in self.model])
            QMessageBox.information(self,'Export complete',path)
        except Exception as e:QMessageBox.warning(self,'Export failed',str(e))
    def import_csv(self):
        path,_=QFileDialog.getOpenFileName(self,'Import CSV','','CSV (*.csv)')
        if path: self.import_rows(path)
    def import_rows(self,path):
        try:
            if self.table_name in TRANSACTION_CSV_HEADERS:
                fields, insert = read_transaction_csv(self.store,path,self.table_name)
                if not insert:
                    QMessageBox.information(self,'Nothing to import','The CSV has no non-empty transaction rows.')
                    return
                if QMessageBox.question(self,'Import CSV',
                        f'Import {len(insert)} {self.name.lower()} records?\n'
                        'Existing records are preserved; duplicate entries are not automatically detected.') != QMessageBox.StandardButton.Yes:
                    return
                with self.store.db:
                    self.store.db.executemany(
                        f"INSERT INTO {self.table_name} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",insert)
                self.refresh();self.on_change()
                QMessageBox.information(self,'Imported',f'{len(insert)} records imported.')
                return
            with open(path,encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
            if not rows:raise ValueError('CSV is empty')
            wanted={c for c,_,_ in self.model};headers=set(rows[0]);
            if not wanted.issubset(headers):raise ValueError('Expected exact database column headers: '+', '.join(sorted(wanted)))
            cols=[c for c,_,_ in self.model];insert=[]
            for idx,row in enumerate(rows,2):
                values=[]
                for c,_,typ in self.model:
                    v=row[c].strip() if row[c] else None
                    if typ=='money':v=cents(v or 0)
                    elif typ in ('date',) and v:v=iso(v)
                    elif typ in ('integer','year','bool') and v:v=int(v)
                    elif typ in LOOKUPS and v:v=int(v)
                    elif typ=='decimal':v=str(Decimal(v or 0))
                    values.append(v)
                validate_record(self.store,self.table_name,dict(zip(cols,values)))
                insert.append(tuple(values))
            if QMessageBox.question(self,'Import CSV',f'Insert {len(insert)} records into {self.name}? Existing records are preserved.')!=QMessageBox.StandardButton.Yes:return
            with self.store.db:
                self.store.db.executemany(f"INSERT INTO {self.table_name} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",insert)
            self.refresh();self.on_change();QMessageBox.information(self,'Imported',f'{len(insert)} records imported.')
        except Exception as e:QMessageBox.warning(self,'Import failed (no partial import)',str(e))

# Human-readable semicolon-delimited templates supplied by the user. The database
# remains empty until a CSV is selected and its import explicitly confirmed.
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

# Excel import is opt-in, with explicit user confirmation. Unsupported sheets are never automatically imported.
EXCEL_MAP={
 'A_Incomes':('incomes',{'Date':'tx_date','Amount':'amount','Group':'category_id','Providers':'provider','Notes':'notes'}),
 'B_Expences':('expenses',{'Date':'tx_date','Amount':'amount','Group':'category_id','Notes':'notes','PaymentSource':'source_account_id','Median':'payment_method_id','Seller':'seller','InvoiceTo':'invoice_to','Fail?':'status'}),
 'C_Sources':('snapshots',{'Date':'snapshot_date','Source':'account_id','Amount':'amount','Notes':'notes'}),
 'D_Invests':('investments',{'Date':'tx_date','Product':'asset_id','Units Buy':'buy_units','Units Sell':'sell_units','Unit price (EUR)':'unit_price','Expences / Fees (EUR)':'fees','Remarks':'notes'})}

class ExcelImportDialog(QDialog):
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.setWindowTitle('Excel import — optional, no automatic loading');self.resize(900,540)
        lay=QVBoxLayout(self);info=QLabel('Choose an existing workbook and one register to import. Category/account/asset names must be created first.\nReview all rows before committing; original workbook is never modified.');info.setWordWrap(True);lay.addWidget(info)
        top=QHBoxLayout();lay.addLayout(top);self.path=QLineEdit();top.addWidget(self.path);b=QPushButton('Browse');b.clicked.connect(self.browse);top.addWidget(b)
        self.sheet=QComboBox();self.sheet.addItems(EXCEL_MAP);self.sheet.currentIndexChanged.connect(self.preview);top.addWidget(self.sheet)
        self.grid=QTableWidget();lay.addWidget(self.grid)
        self.result=QLabel('No data imported.');lay.addWidget(self.result)
        bot=QHBoxLayout();lay.addLayout(bot);preview=QPushButton('Preview');preview.clicked.connect(self.preview);bot.addWidget(preview);import_btn=QPushButton('Import selected sheet');import_btn.clicked.connect(self.commit);bot.addWidget(import_btn)
        self.warnings=[];self.records=[]
    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'Select workbook','','Excel workbooks (*.xlsx)')
        if path:self.path.setText(path);self.preview()
    def preview(self,*_):
        self.records=[];self.warnings=[];self.grid.clear();self.grid.setRowCount(0)
        p=self.path.text().strip()
        if not p:return
        try:
            from openpyxl import load_workbook
            wb=load_workbook(p,read_only=True,data_only=True)
            sheet=self.sheet.currentText()
            if sheet not in wb.sheetnames:raise ValueError(f'Workbook has no sheet {sheet}')
            ws=wb[sheet];raw=ws.iter_rows(values_only=True);head=[str(x or '').strip().replace('\n',' ') for x in next(raw)];mapping=EXCEL_MAP[sheet][1]
            normalized={s.lower().replace(' ','').replace('/',''):i for i,s in enumerate(head)}
            def get(row,key):
                k=key.lower().replace(' ','').replace('/','');i=normalized.get(k)
                return row[i] if i is not None and i<len(row) else None
            table=EXCEL_MAP[sheet][0];self.table_name=table
            model=next(fields for t,fields in MODELS.values() if t==table)
            for rownum,row in enumerate(raw,2):
                if all(v is None for v in row):continue
                try:
                    d={}
                    for old,new in mapping.items():
                        v=get(row,old)
                        if new in ('category_id','account_id','source_account_id','payment_method_id','asset_id'):
                            if v is None or str(v).strip()=='':d[new]=None;continue
                            t={'category_id':'categories','account_id':'accounts','source_account_id':'accounts','payment_method_id':'payment_methods','asset_id':'investment_assets'}[new]
                            scope="AND kind='income'" if table=='incomes' and new=='category_id' else "AND kind='expense'" if table=='expenses' and new=='category_id' else ''
                            lookup=self.store.query(f'SELECT id FROM {t} WHERE name=? {scope}',(str(v).strip(),))
                            if not lookup:raise ValueError(f'{old}={v!r} not found in {t} (create it first)')
                            d[new]=lookup[0][0]
                        elif new in ('tx_date','snapshot_date'):d[new]=iso(v)
                        elif new in ('amount','fees'):d[new]=cents(v or 0)
                        else:d[new]=str(v).strip() if v is not None else ''
                    if table=='investments':
                        bu=Decimal(str(get(row,'Units Buy') or 0));se=Decimal(str(get(row,'Units Sell') or 0))
                        if (bu>0)==(se>0):raise ValueError('Exactly one of Units Buy or Units Sell must be positive')
                        d['action']='Buy' if bu>0 else 'Sell';d['units']=str(bu if bu>0 else se)
                        d.pop('buy_units',None);d.pop('sell_units',None)
                    if table=='snapshots' and d['account_id'] is None:raise ValueError('Account is required')
                    if table=='investments' and d['asset_id'] is None:raise ValueError('Investment asset is required')
                    validate_record(self.store,table,d)
                    self.records.append((rownum,d))
                except Exception as e:self.warnings.append(f'Row {rownum}: {e}')
            wb.close()
            columns=[col for col,_,_ in model];self.grid.setColumnCount(len(columns)+1);self.grid.setHorizontalHeaderLabels(['Excel row']+columns);self.grid.setRowCount(min(250,len(self.records)))
            for i,(n,d) in enumerate(self.records[:250]):
                for j,val in enumerate([n]+[d.get(c,'') for c in columns]):self.grid.setItem(i,j,QTableWidgetItem(str(val)))
            self.result.setText(f'{len(self.records)} valid rows; {len(self.warnings)} rejected rows. Preview shows first 250.\n'+'\n'.join(self.warnings[:6]))
        except Exception as e: self.result.setText(f'Preview failed: {e}')
    def commit(self):
        if not self.records:QMessageBox.warning(self,'Nothing to import','Preview a workbook with valid records first.');return
        if self.warnings:QMessageBox.warning(self,'Resolve errors',f'{len(self.warnings)} rows have problems. Import is disabled until all rows pass validation.\n'+'\n'.join(self.warnings[:8]));return
        if QMessageBox.question(self,'Confirm import',f'Import {len(self.records)} records into {self.table_name}? This inserts new records; it does not overwrite existing data.')!=QMessageBox.StandardButton.Yes:return
        try:
            cols=list(self.records[0][1]);values=[tuple(d.get(c) for c in cols) for _,d in self.records]
            with self.store.db:self.store.db.executemany(f"INSERT INTO {self.table_name} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",values)
            QMessageBox.information(self,'Imported',f'Imported {len(values)} records successfully.');self.accept()
        except Exception as e:QMessageBox.warning(self,'Import failed: transaction rolled back',str(e))

def month_bounds(year,month):
    start=f'{year:04d}-{month:02d}-01';end=f'{year:04d}-{month:02d}-{calendar.monthrange(year,month)[1]:02d}'
    return start,end

def get_monthly(store,year,month):
    first,last=month_bounds(year,month)
    prev=date(year,month,1)-timedelta(days=1)
    prevmonth_start,_=month_bounds(prev.year,prev.month)
    # End-of-month snapshot amount per account. Uses latest snapshot within each calendar month,
    # not interpolation; snapshots dated other months are never carried forward silently.
    def balance_for(start,end):
        rows=store.query('''SELECT s.account_id,s.amount FROM snapshots s
           WHERE s.snapshot_date=(SELECT MAX(t.snapshot_date) FROM snapshots t
           WHERE t.account_id=s.account_id AND t.snapshot_date BETWEEN ? AND ?)''',(start,end))
        return sum(r['amount'] for r in rows),len(rows)
    opening,opening_n=balance_for(prevmonth_start,prev.isoformat())
    closing,closing_n=balance_for(first,last)
    inc=store.scalar('SELECT COALESCE(SUM(amount),0) FROM incomes WHERE tx_date BETWEEN ? AND ?',(first,last))
    exp=store.scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE tx_date BETWEEN ? AND ?',(first,last))
    return dict(opening=opening,closing=closing,income=inc,expenses=exp,unregistered=opening-closing+inc-exp,opening_n=opening_n,closing_n=closing_n)

def annual_summary(store,year):
    out=[]
    for kind,table in [('income','incomes'),('expense','expenses')]:
        rows=store.query(f'''SELECT c.name, c.id, b.amount AS planned,
          (SELECT COALESCE(SUM(t.amount),0) FROM {table} t WHERE t.category_id=c.id AND t.tx_date>=? AND t.tx_date<?) AS actual
          FROM categories c LEFT JOIN budgets b ON b.category_id=c.id AND b.kind=? AND b.year=?
          WHERE c.kind=? AND (b.id IS NOT NULL OR EXISTS(SELECT 1 FROM {table} t WHERE t.category_id=c.id AND t.tx_date>=? AND t.tx_date<?)) ORDER BY c.name''',
          (f'{year}-01-01',f'{year+1}-01-01',kind,year,kind,f'{year}-01-01',f'{year+1}-01-01'))
        out.extend([(kind,r['name'],r['planned'] or 0,r['actual']) for r in rows])
    roots=store.query("SELECT id,name FROM categories WHERE kind='expense' AND parent_id IS NULL ORDER BY name")
    for root in roots:
        children=store.query('SELECT id FROM categories WHERE parent_id=?',(root['id'],))
        if children:
            ids=[r['id'] for r in children]
            marks=','.join('?' for _ in ids)
            planned=store.scalar(f'SELECT COALESCE(SUM(amount),0) FROM budgets WHERE year=? AND kind=? AND category_id IN ({marks})',(year,'expense',*ids))
            actual=store.scalar(f'SELECT COALESCE(SUM(amount),0) FROM expenses WHERE tx_date>=? AND tx_date<? AND category_id IN ({marks})',(f'{year}-01-01',f'{year+1}-01-01',*ids))
            out.append(('expense',root['name']+' (root total)',planned,actual))
    return out

def add_month(d,delta):
    m=(d.month-1+delta);y=d.year+m//12;m=m%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))

def debt_schedule(store):
    entries=[]
    for d in store.query('SELECT * FROM debts ORDER BY first_due'):
        remaining=d['total']-store.scalar('SELECT COALESCE(SUM(amount),0) FROM debt_payments WHERE debt_id=?',(d['id'],))
        count=max(1,d['installments']);each,extra=divmod(d['total'],count)
        for i in range(count):entries.append((d['name'],d['kind'],add_month(date.fromisoformat(d['first_due']),i).isoformat(),each+(1 if i<extra else 0),max(0,remaining)))
    return entries

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

class GlobalSearchPage(QWidget):
    """Read-only global search. Every indexed field is searchable including linked lookup labels."""
    def __init__(self,store):
        super().__init__();self.store=store
        v=QVBoxLayout(self);bar=QHBoxLayout();v.addLayout(bar)
        self.text=QLineEdit();self.text.setPlaceholderText('Search all text, IDs, dates, categories, account names…');bar.addWidget(self.text,4)
        self.amount=QLineEdit();self.amount.setPlaceholderText('Amount € (optional)');bar.addWidget(self.amount,1)
        self.kind=QComboBox();self.kind.addItem('All registers','*')
        for name in MODELS:self.kind.addItem(name,name)
        bar.addWidget(self.kind)
        run=QPushButton('Search');run.clicked.connect(self.search);bar.addWidget(run)
        clear=QPushButton('Clear');clear.clicked.connect(self.clear);bar.addWidget(clear)
        self.text.returnPressed.connect(self.search);self.amount.returnPressed.connect(self.search)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Register','ID','Date / Year','Amount (€)','Matched record / fields'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True);v.addWidget(self.table)
        self.status=QLabel('Enter search text and/or amount. No database records are modified.');v.addWidget(self.status)
    def clear(self):
        self.text.clear();self.amount.clear();self.kind.setCurrentIndex(0);self.table.setRowCount(0);self.status.setText('Search cleared.')
    def search(self):
        term=self.text.text().strip().casefold();amt=self.amount.text().strip()
        if not term and not amt:
            self.table.setRowCount(0);self.status.setText('Enter text and/or amount.');return
        try: amount=cents(amt) if amt else None
        except ValueError as e:QMessageBox.warning(self,'Invalid amount',str(e));return
        hits=[];limit=2000
        models=[(n,t,f) for n,(t,f) in MODELS.items() if self.kind.currentData() in ('*',n)]
        for name,table,fields in models:
            cols=['id']+[f[0] for f in fields]
            for r in self.store.query(f'SELECT {",".join(cols)} FROM {table} ORDER BY id DESC LIMIT 10000'):
                info=[(label,display(self.store,r[col],typ)) for col,label,typ in fields]
                matching_amount=any(typ=='money' and r[col] is not None and int(r[col])==amount for col,_,typ in fields) if amount is not None else True
                matching_text=any(term in str(val).casefold() for _,val in info) or term in str(r['id']) or term in name.casefold() if term else True
                if matching_amount and matching_text:
                    dt=next((str(r[col]) for col,_,typ in fields if typ=='date' and r[col]),str(r['year']) if 'year' in r.keys() else '')
                    displayed_amount=', '.join(money_text(r[col]) for col,_,typ in fields if typ=='money' and r[col] is not None)
                    hits.append((name,str(r['id']),dt,displayed_amount,' | '.join(f'{label}: {val}' for label,val in info if str(val))))
                    if len(hits)>=limit:break
            if len(hits)>=limit:break
        hits.sort(key=lambda x:(x[2],int(x[1])),reverse=True)
        self.table.setRowCount(len(hits))
        for i,row in enumerate(hits):
            for j,v in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(v))
        self.status.setText(f'{len(hits)} matching records'+(' (limit reached)' if len(hits)>=limit else ''))

class ChartsPage(QWidget):
    def __init__(self,store):
        super().__init__();self.store=store;v=QVBoxLayout(self);bar=QHBoxLayout();v.addLayout(bar)
        self.choice=QComboBox();self.choice.addItems(['Monthly income vs registered expenses','Unregistered daily expenses by month','Monthly source balances','Annual income vs expenses','Expenses by root category','Budget vs actual expenses'])
        self.year=QSpinBox();self.year.setRange(1900,2200);self.year.setValue(date.today().year)
        bar.addWidget(QLabel('Chart'));bar.addWidget(self.choice,3);bar.addWidget(QLabel('Year'));bar.addWidget(self.year)
        b=QPushButton('Refresh');b.clicked.connect(self.refresh);bar.addWidget(b)
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            self.figure=Figure(figsize=(9,5),tight_layout=True);self.canvas=FigureCanvasQTAgg(self.figure);v.addWidget(self.canvas)
        except ImportError:
            self.figure=None;v.addWidget(QLabel('Install matplotlib to display charts: pip install matplotlib'))
        self.choice.currentIndexChanged.connect(self.refresh);self.year.valueChanged.connect(self.refresh)
        self.refresh()
    def refresh(self,*_):
        if self.figure is None:return
        self.figure.clear();ax=self.figure.add_subplot(111);year=self.year.value();choice=self.choice.currentIndex()
        months=list(range(1,13));names=[calendar.month_abbr[m] for m in months]
        monthly=[get_monthly(self.store,year,m) for m in months]
        if choice==0:
            ax.bar([m-.2 for m in months],[d['income']/100 for d in monthly],width=.4,label='Income')
            ax.bar([m+.2 for m in months],[d['expenses']/100 for d in monthly],width=.4,label='Registered expenses');ax.legend()
        elif choice==1:
            ax.bar(months,[d['unregistered']/100 for d in monthly]);ax.set_title('Estimated unregistered spending — snapshot formula')
        elif choice==2:
            ax.plot(months,[d['closing']/100 if d['closing_n'] else float('nan') for d in monthly],marker='o');ax.set_title('Month-end source balances (missing months omitted)')
        elif choice==3:
            ys=list(range(year-4,year+1));ax.bar([y-.2 for y in ys],[self.store.scalar('SELECT COALESCE(SUM(amount),0) FROM incomes WHERE tx_date BETWEEN ? AND ?',(f'{y}-01-01',f'{y}-12-31'))/100 for y in ys],width=.4,label='Income')
            ax.bar([y+.2 for y in ys],[self.store.scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE tx_date BETWEEN ? AND ?',(f'{y}-01-01',f'{y}-12-31'))/100 for y in ys],width=.4,label='Expenses');ax.set_xticks(ys);ax.legend()
        elif choice==4:
            data=expense_root_totals(self.store,year)
            if data:
                ax.barh([x[0] for x in data],[x[1]/100 for x in data]);ax.invert_yaxis()
            ax.set_title('Registered expenses grouped by root category')
        elif choice==5:
            data=[r for r in annual_summary(self.store,year) if r[0]=='expense' and not r[1].endswith(' (root total)')]
            data=sorted(data,key=lambda r:r[3],reverse=True)[:15]
            xx=list(range(len(data)))
            ax.barh([x-.2 for x in xx],[r[2]/100 for r in data],height=.4,label='Planned')
            ax.barh([x+.2 for x in xx],[r[3]/100 for r in data],height=.4,label='Actual')
            ax.set_yticks(xx,[r[1] for r in data]);ax.invert_yaxis();ax.legend()
        if choice in (0,1,2):ax.set_xticks(months,names)
        ax.set_ylabel('EUR' if choice!=4 else '');ax.grid(axis='y',alpha=.2);self.canvas.draw_idle()

def expense_root_totals(store,year):
    rows=store.query("""SELECT root.name AS name, COALESCE(SUM(e.amount),0) total
        FROM expenses e JOIN categories leaf ON leaf.id=e.category_id
        JOIN categories root ON root.id=leaf.parent_id
        WHERE e.tx_date BETWEEN ? AND ? GROUP BY root.id ORDER BY total DESC""",
        (f'{year}-01-01',f'{year}-12-31'))
    return [(r['name'],r['total']) for r in rows]

class MainWindow(QMainWindow):
    def __init__(self,store):
        super().__init__();self.store=store;self.setWindowTitle('PanBank v3.1 - Personal Financial Assistant (by G. Tsakalos)');self.resize(1320,830)
        self.tabs=QTabWidget();self.setCentralWidget(self.tabs);self.pages={}
        self.dashboard=self.make_dashboard();self.tabs.addTab(self.dashboard,'Dashboard')
        self.budget_page=self.make_budget();self.tabs.addTab(self.budget_page,'Budget vs Actual')
        self.search_page=GlobalSearchPage(store);self.tabs.addTab(self.search_page,'Global Search')
        for group,names in [('Transactions',['Income register','Expense register']),('Sources',['Accounts','Payment methods','Monthly snapshots']),('Investments',['Investment assets','Investment transactions','Investment valuations']),('Debts',['Debts','Debt payments']),('Settings',['Categories','Annual budgets'])]:
            nested=QTabWidget()
            for name in names:
                page=CrudPage(store,name,self.refresh_reports);nested.addTab(page,name);self.pages[name]=page
            self.tabs.addTab(nested,group)
        self.charts_page=ChartsPage(store);self.tabs.addTab(self.charts_page,'Charts')
        self.portfolio_page=self.make_portfolio();self.tabs.addTab(self.portfolio_page,'Portfolio')
        self.debt_page=self.make_debt_forecast();self.tabs.addTab(self.debt_page,'Debt schedule')
        self.tools=self.make_tools();self.tabs.addTab(self.tools,'Data & Backups')
        self.help_button=QToolButton(self);self.help_button.setText('Help ▾');self.help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.help_menu=QMenu(self);self.help_menu.addAction('Using PanBank / Financial calculations',self.show_help)
        self.help_menu.addAction('About PanBank',lambda:QMessageBox.information(self,'About','PanBank v3.1 - Personal financial assistant (by G. Tsakalos)\n(SQLite / PySide6 / snapshot-based balances)'))
        self.help_button.setMenu(self.help_menu);self.tabs.setCornerWidget(self.help_button,Qt.Corner.TopRightCorner)
        self.tabs.currentChanged.connect(lambda _:self.refresh_reports());self.refresh_reports()
    def show_help(self):
        dlg=QDialog(self);dlg.setWindowTitle('PanBank Help');dlg.resize(770,580)
        layout=QVBoxLayout(dlg);browser=QTextBrowser();layout.addWidget(browser)
        browser.setHtml("""<h2>PanBank financial logic</h2>
        <p><b>Unregistered daily expenses (monthly)</b> = opening source balances − closing source balances + registered incomes − registered expenses.</p>
        <p>Opening and closing balances are taken exclusively from the manually entered <b>Monthly snapshots</b> records. Missing snapshots are NOT zero known balances. Investments and internal transfers do not automatically adjust this formula.</p>
        <h3>Registers and filters</h3><p>Use <b>Filter</b> in each register to restrict visible records without deleting data. The Global Search tab searches all registers by text and/or exact EUR amount, with a register selector. Newest dated records appear first.</p>
        <h3>Expense categories</h3><p>Configure expense root groups and second-level subcategories in Settings → Categories. Root categories are reporting totals and cannot be chosen for new expense records. Enter annual expense budgets at the second level as well. No root-level expense entries are permitted, including imported records.</p>
        <h3>Budgets</h3><p>Annual planned figures are manually entered, while actuals are calculated from income and expense transactions. Root expense totals are calculated from their subcategories.</p>
        <h3>Investments</h3><p>Buy and sell records yield FIFO realized gains. Manually entered valuations determine unrealized gains; payouts are listed separately.</p>
        <h3>Debts</h3><p>Taxes and loans can be scheduled as one-time debts or monthly installments; actual payments are recorded separately.</p>
        <h3>Database and backups</h3><p>By default panbank.sqlite3 resides alongside panbank.py. The Data & Backups tab supports choosing another folder, optional Excel importing, exports and backups.</p>
        <p><b>Data safety:</b> Before upgrading, back up your existing SQLite file. Never replace it with an empty sample database.</p>""")
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dlg.reject);layout.addWidget(close);dlg.exec()
    def make_dashboard(self):
        page=QWidget();v=QVBoxLayout(page);bar=QHBoxLayout();v.addLayout(bar)
        self.month=QComboBox();self.month.addItems([calendar.month_name[i] for i in range(1,13)]);self.month.setCurrentIndex(date.today().month-1);bar.addWidget(QLabel('Month'));bar.addWidget(self.month)
        self.year=QSpinBox();self.year.setRange(1900,2200);self.year.setValue(date.today().year);bar.addWidget(QLabel('Year'));bar.addWidget(self.year)
        b=QPushButton('Refresh calculation');b.clicked.connect(self.refresh_reports);bar.addWidget(b);bar.addStretch()
        self.summary=QLabel();self.summary.setTextFormat(Qt.TextFormat.RichText);self.summary.setStyleSheet('font-size: 16px;line-height: 1.7');v.addWidget(self.summary)
        self.monthly_table=QTableWidget(12,7);self.monthly_table.setHorizontalHeaderLabels(['Month','Start balances (€)','End balances (€)','Incomes (€)','Registered expenses (€)','Unregistered (€)','Snapshots (start/end)']);self.monthly_table.horizontalHeader().setStretchLastSection(True);v.addWidget(self.monthly_table)
        self.month.currentIndexChanged.connect(self.refresh_reports);self.year.valueChanged.connect(self.refresh_reports)
        return page
    def make_budget(self):
        w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel('Annual budget compared to registered transaction totals (unregistered daily expenses are separate).'))
        self.budget_table=QTableWidget();self.budget_table.setColumnCount(5);self.budget_table.setHorizontalHeaderLabels(['Type','Category','Planned (€)','Actual (€)','Difference (€)']);l.addWidget(self.budget_table);return w
    def make_portfolio(self):
        w=QWidget();v=QVBoxLayout(w);v.addWidget(QLabel('FIFO realized gains; latest manual valuation for unrealized gains. Dividends/income shown separately.'))
        self.portfolio_table=QTableWidget();self.portfolio_table.setColumnCount(8);self.portfolio_table.setHorizontalHeaderLabels(['Asset','Held units','Cost basis (€)','Latest value (€)','Realized (€)','Unrealized (€)','Distributions (€)','Validation']);v.addWidget(self.portfolio_table);return w
    def make_debt_forecast(self):
        w=QWidget();v=QVBoxLayout(w);self.debt_total=QLabel();v.addWidget(self.debt_total)
        self.debt_table=QTableWidget();self.debt_table.setColumnCount(5);self.debt_table.setHorizontalHeaderLabels(['Debt','Type','Due date','Scheduled installment (€)','Remaining debt (€)']);v.addWidget(self.debt_table)
        v.addWidget(QLabel('Scheduled installments are forecasts, not automatically recorded expenses. Record actual payment separately in Expense register and Debt payments as appropriate.'))
        return w
    def make_tools(self):
        w=QWidget();v=QVBoxLayout(w);v.addWidget(QLabel('New database begins empty. Excel files are never imported automatically.'))
        for label,func in [('Import from original Excel (optional)',self.excel_import),('Back up SQLite database',self.backup),('Export all tables to Excel',self.export_excel),('Refresh all tables',self.refresh_all)]:
            btn=QPushButton(label);btn.clicked.connect(func);v.addWidget(btn)
        v.addWidget(QLabel('Active database file:'))
        self.db_path_label=QLabel(str(self.store.path));self.db_path_label.setWordWrap(True)
        self.db_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.db_path_label)
        btn=QPushButton('Select database folder…');btn.clicked.connect(self.choose_database_folder);v.addWidget(btn)
        btn=QPushButton('Use application folder (default)');btn.clicked.connect(lambda:self.switch_database(APP_DIR));v.addWidget(btn)
        v.addWidget(QLabel('Changing folders does not move or copy your existing database. '
                           'A folder without panbank.sqlite3 will start with a NEW empty database.'))
        v.addStretch();return w
    def choose_database_folder(self):
        directory=QFileDialog.getExistingDirectory(self,'Choose database folder',str(self.store.path.parent))
        if directory:self.switch_database(Path(directory))
    def switch_database(self,directory):
        directory=Path(directory).expanduser().resolve()
        path=directory/DB_FILENAME
        if path==self.store.path.resolve():
            QMessageBox.information(self,'Database unchanged','This is already the active database folder.')
            return
        if not path.is_file():
            reply=QMessageBox.question(self,'Create empty database?',
                f'No PanBank database exists in:\n{directory}\n\nCreate a NEW empty database there?\n'
                'Your current database will remain untouched.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply!=QMessageBox.StandardButton.Yes:return
        try:
            new_store=Store(path)
            new_window=MainWindow(new_store)
            try:save_data_dir(directory)
            except OSError as exc:
                new_store.close()
                new_window.close()
                QMessageBox.warning(self,'Cannot save preference',
                    f'Could not save the database location in {CONFIG_PATH}:\n{exc}\n'
                    'The previous database will remain active.')
                return
        except (OSError,sqlite3.Error) as exc:
            QMessageBox.warning(self,'Cannot open database',str(exc))
            return
        # Keep the new window referenced by the QApplication when the old one closes.
        QApplication.instance().panbank_window=new_window
        new_window.show()
        self.close()
        self.store.close()
    def refresh_reports(self,*_):
        year=self.year.value();month=self.month.currentIndex()+1
        d=get_monthly(self.store,year,month)
        warning=('  ⚠ Missing opening/closing snapshots (enter month-end balances for both months)' if not d['opening_n'] or not d['closing_n'] else '')
        self.summary.setText(f'<b>{calendar.month_name[month]} {year}</b>  |  '
            f'Opening: {eur(d["opening"])}  |  Closing: {eur(d["closing"])}  |  '
            f'Income: {eur(d["income"])}  |  Expenses: {eur(d["expenses"])}<br>'
            f'<b>Estimated unregistered daily expenses: {eur(d["unregistered"])}</b>' + warning)
        for i in range(1,13):
            d=get_monthly(self.store,year,i)
            cells=[calendar.month_abbr[i],money_text(d['opening']),money_text(d['closing']),money_text(d['income']),money_text(d['expenses']),money_text(d['unregistered']),f"{d['opening_n']} / {d['closing_n']}"]
            for j,value in enumerate(cells):self.monthly_table.setItem(i-1,j,QTableWidgetItem(str(value)))
        b=annual_summary(self.store,year);self.budget_table.setRowCount(len(b))
        for i,(kind,cat,planned,actual) in enumerate(b):
            for j,value in enumerate([kind,cat,money_text(planned),money_text(actual),money_text(planned-actual)]):self.budget_table.setItem(i,j,QTableWidgetItem(str(value)))
        p=portfolio(self.store);self.portfolio_table.setRowCount(len(p))
        for i,r in enumerate(p):
            vals=[r['name'],str(r['units']),str(round(r['cost'],2)),str(round(r['value'],2)) if r['value'] is not None else 'No valuation',str(round(r['realized'],2)),str(round(r['unrealized'],2)) if r['unrealized'] is not None else '',str(round(r['distributions'],2)),'; '.join(r['errors'])]
            for j,value in enumerate(vals):self.portfolio_table.setItem(i,j,QTableWidgetItem(str(value)))
        schedule=debt_schedule(self.store);self.debt_table.setRowCount(len(schedule))
        remaining=self.store.scalar('SELECT COALESCE(SUM(total),0) FROM debts')-self.store.scalar('SELECT COALESCE(SUM(amount),0) FROM debt_payments')
        self.debt_total.setText(f'Total remaining debt (recorded payments deducted): {eur(max(0,remaining))}')
        for i,row in enumerate(schedule):
            for j,value in enumerate(row):self.debt_table.setItem(i,j,QTableWidgetItem(money_text(value) if j>=3 else str(value)))
    def refresh_all(self):
        for page in self.pages.values():page.refresh()
        self.refresh_reports()
        self.charts_page.refresh()
        self.charts_page.refresh()
    def excel_import(self):
        if ExcelImportDialog(self.store,self).exec()==QDialog.DialogCode.Accepted:self.refresh_all()
    def backup(self):
        default=f'panbank-backup-{date.today().isoformat()}.sqlite3';p,_=QFileDialog.getSaveFileName(self,'SQLite backup',default,'SQLite database (*.sqlite3)')
        if p:
            try:self.store.backup(p);QMessageBox.information(self,'Backup complete',p)
            except Exception as e:QMessageBox.warning(self,'Backup failed',str(e))
    def export_excel(self):
        p,_=QFileDialog.getSaveFileName(self,'Export all data','PanBank_Export.xlsx','Excel (*.xlsx)')
        if not p:return
        try:
            from openpyxl import Workbook
            wb=Workbook();wb.remove(wb.active)
            for name,(table,model) in MODELS.items():
                sheet=wb.create_sheet(table[:31]);cols=['id']+[c for c,_,_ in model];sheet.append(cols)
                for r in self.store.query(f"SELECT {','.join(cols)} FROM {table} ORDER BY id"):
                    sheet.append([money_text(r[c]) if c!='id' and next((t for col,_,t in model if col==c),'')=='money' else r[c] for c in cols])
                sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
                for col in sheet.columns:
                    letter=col[0].column_letter;sheet.column_dimensions[letter].width=min(34,max(12,max(len(str(x.value or '')) for x in list(col)[:120])+2))
            wb.save(p);QMessageBox.information(self,'Export complete',p)
        except Exception as e:QMessageBox.warning(self,'Export failed',str(e))

def main():
    app=QApplication(sys.argv);app.setStyle('Fusion')
    app.setStyleSheet('QTabWidget::pane {border:1px solid #777777;} QTableWidget {gridline-color:#aaaaaa;} QPushButton {padding:6px 10px;} QLineEdit,QComboBox,QDateEdit,QSpinBox {padding:4px;}')
    store=Store();window=MainWindow(store);app.panbank_window=window;window.show()
    rc=app.exec();app.panbank_window.store.close();sys.exit(rc)

if __name__=='__main__':main()
