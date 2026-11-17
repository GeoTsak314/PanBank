# PanBank Web v4.4.1
# single web entrypoint (desktop + mobile) - by G. Tsakalos
from __future__ import annotations
import csv, io, os, secrets, sqlite3, json, tempfile, hashlib, hmac, argparse, getpass, stat, calendar
from datetime import date, timedelta, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from core import (Store,DB_PATH,CONFIG_FILE,SETTINGS_FILE,read_app_settings,MODELS,LOOKUPS,OPTIONS,keyboard_cents,keyboard_money_text,
    cents,money_text,eur,iso,validate_record,get_monthly,year_reconciliation,budget_rows,
    investment_cashflows,debt_payment_cashflows,portfolio,debt_schedule,read_transaction_csv)

APP_DIR=Path(__file__).resolve().parent
# Process the one-time setup command before enforcing credentials.
# Setup implementation is declared below; run it at bottom after imports instead.
SETUP_MODE = '--setup' in __import__('sys').argv
# Save credentials as a salted PBKDF2 hash, never plain text.
# Environmental credentials take precedence for unattended/systemd deployments.
SETTINGS=read_app_settings()
PASSWORD=os.environ.get('PANBANK_PASSWORD','')
PASSWORD_HASH=SETTINGS.get('password_hash','') if not PASSWORD else ''
SECRET=os.environ.get('PANBANK_SESSION_SECRET') or SETTINGS.get('session_secret','')
if not SETUP_MODE and (not (PASSWORD or PASSWORD_HASH) or not SECRET or len(SECRET)<32):
    raise RuntimeError('PanBank needs one-time setup. Run: python panbank.py --setup')

def hash_password(password, salt=None, iterations=600000):
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}'

def verify_password(password):
    if PASSWORD:
        return hmac.compare_digest(password,PASSWORD)
    try:
        algorithm,iterations,salt,digest=PASSWORD_HASH.split('$')
        if algorithm!='pbkdf2_sha256' or not 100000<=int(iterations)<=2000000:return False
        result=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),int(iterations))
        return hmac.compare_digest(result,bytes.fromhex(digest))
    except (ValueError,TypeError):return False

def save_app_settings(data):
    """Save private local config atomically; refuse symlink destinations."""
    if SETTINGS_FILE.is_symlink():raise ValueError('Refusing symlink settings file')
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=APP_DIR,prefix='.panbank-settings-',suffix='.tmp',delete=False) as f:
        temporary=Path(f.name)
        try:
            if os.name!='nt':os.chmod(temporary,0o600)
            json.dump(data,f,indent=2,ensure_ascii=False)
        finally:pass
    try:
        os.replace(temporary,SETTINGS_FILE)
        if os.name!='nt':os.chmod(SETTINGS_FILE,0o600)
    finally:temporary.unlink(missing_ok=True)

def first_setup():
    """Interactive one-time configuration; --setup can also reset a login password."""
    prior=read_app_settings()
    print('PanBank Web setup — settings stay on this computer/server.')
    one=getpass.getpass('New PanBank login password: ')
    two=getpass.getpass('Repeat password: ')
    if one!=two:raise ValueError('Passwords did not match')
    saved=dict(prior)
    saved['password_hash']=hash_password(one)
    saved['session_secret']=prior.get('session_secret') or secrets.token_urlsafe(48)
    current=prior.get('database_directory',str(APP_DIR))
    directory=input(f'Database directory [{current}]: ').strip() or current
    folder=Path(directory).expanduser().resolve()
    if not folder.is_dir():raise ValueError(f'Database directory does not exist: {folder}')
    saved['database_directory']=str(folder)
    save_app_settings(saved)
    print(f'Settings saved in {SETTINGS_FILE} (password stored as a salted hash).')
    print('On subsequent runs, use: python panbank.py')

app=FastAPI(title='PanBank Web v4.4.1 by G.Tsakalos',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SessionMiddleware,secret_key=SECRET,same_site='strict',https_only=os.getenv('PANBANK_HTTPS','0')=='1',max_age=12*3600)
if os.getenv('PANBANK_ALLOWED_HOSTS'):
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[x.strip() for x in os.environ['PANBANK_ALLOWED_HOSTS'].split(',')])
templates=Jinja2Templates(directory=str(APP_DIR/'templates'))
templates.env.filters['eur']=eur
templates.env.filters['money']=money_text
templates.env.filters['keyboard_money']=keyboard_money_text

def european_date(value):
    """Display ISO SQLite dates as DD/MM/YYYY; do not alter stored ISO dates."""
    if not value:return ''
    try:return date.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:return str(value)

templates.env.filters['eurodate']=european_date

def selectable_years(s=None):
    """Useful historic + upcoming years; never require manual entry in year fields."""
    now=date.today().year
    return list(range(now+5,1899,-1))


# Short-lived DB connections per request. Never share a sqlite3 connection across threads.
def store():
    s=Store(DB_PATH)
    from core import migrate_web
    migrate_web(s)
    return s

def authorized(req):return req.session.get('logged_in') is True

def require(req):
    if not authorized(req):raise HTTPException(status_code=401,detail='Please log in first')

def require_csrf(req,value):
    require(req)
    if not value or not secrets.compare_digest(str(value),req.session.get('csrf','')):
        raise HTTPException(status_code=403,detail='Invalid CSRF token')

def context(req,**kwargs):
    return {'request':req,'csrf':req.session.get('csrf',''),**kwargs}

def html(req,template_name,**kwargs):return templates.TemplateResponse(req,template_name,context(req,**kwargs))

def jump(url):return RedirectResponse(url,status_code=303)

NAV=[('Dashboard','/'),('Course','/course'),('Transactions','/records/Income%20register'),
     ('Investments','/investments'),('Debt','/debt'),('Settings','/records/Categories'),
     ('Charts','/charts'),('Global Search','/search'),('Data & Backups','/tools')]
GROUPS={
 'Course':['Budget vs Actual','Annual budgets'],
 'Transactions':['Income register','Expense register','Monthly snapshots'],
 'Investments':['Portfolio','Investment assets','Investment transactions','Investment valuations'],
 'Debt':['Debts','Debt schedule','Debt payments'],
 'Settings':['Categories','Accounts','Payment methods','Currencies','Database Management'],
}

def extras(s,active,sub=''):
    return dict(nav=NAV,group=active,sub=sub,group_links=GROUPS.get(active,[]),database=str(DB_PATH))

def model_name(value):
    if value not in MODELS:raise HTTPException(status_code=404,detail='Unknown register')
    return MODELS[value]

def lookup_choices(s,typ):
    table,cond=LOOKUPS[typ]
    if typ=='currency_code':
        return [(r['code'],r['code']+' — '+r['name']) for r in s.query('SELECT code,name FROM currencies WHERE active=1 ORDER BY code COLLATE NOCASE')]
    if typ=='parent_category':
        return [(r['id'],r['name']) for r in s.query("SELECT id,name FROM categories WHERE active=1 AND parent_id IS NULL ORDER BY name COLLATE NOCASE")]
    if typ in ('expense_category','income_category','budget_category','any_category'):
        where={
          'expense_category':"c.kind='expense' AND c.active=1 AND c.parent_id IS NOT NULL",
          'income_category':"c.kind='income' AND c.active=1",
          'any_category':'c.active=1',
          'budget_category':"c.active=1 AND ((c.kind='expense' AND c.parent_id IS NOT NULL) OR (c.kind='income' AND NOT EXISTS(SELECT 1 FROM categories child WHERE child.parent_id=c.id)))"
        }[typ]
        rows=s.query('SELECT c.id,c.name,c.kind,p.name AS parent FROM categories c '
          'LEFT JOIN categories p ON p.id=c.parent_id WHERE '+where+
          ' ORDER BY COALESCE(p.name,c.name) COLLATE NOCASE,c.name COLLATE NOCASE')
        return [(r['id'],(r['parent']+' → ' if r['parent'] else '')+r['name']) for r in rows]
    return [(r['id'],r['name']) for r in s.query(f'SELECT id,name FROM {table} WHERE {cond} ORDER BY name COLLATE NOCASE')]

def choices(s,typ):
    if typ in LOOKUPS:return lookup_choices(s,typ)
    if typ in OPTIONS:return [(x,x) for x in OPTIONS[typ]]
    return []

def rendered_val(s,rec,col,typ):
    v=rec[col]
    if typ in LOOKUPS:
        if v is None:return ''
        return next((label for key,label in lookup_choices(s,typ) if str(key)==str(v)),str(v))
    if typ=='money':return money_text(v)
    if typ=='date':return european_date(v)
    if typ=='bool':return 'Yes' if v else 'No'
    return '' if v is None else str(v)

@app.get('/login',response_class=HTMLResponse)
def login_get(req:Request):
    if authorized(req):return jump('/')
    return html(req,'login.html',error='')

@app.post('/login')
def login_post(req:Request,password:str=Form("")):
    if not verify_password(password):
        return html(req,'login.html',error='Incorrect password')
    req.session.clear();req.session['logged_in']=True;req.session['csrf']=secrets.token_urlsafe(32)
    return jump('/')

@app.post('/logout')
def logout(req:Request,csrf:str=Form(...)):
    require_csrf(req,csrf);req.session.clear();return jump('/login')

@app.get('/',response_class=HTMLResponse)
def dashboard(req:Request,year:int|None=None,month:int|None=None):
    require(req)
    today=date.today()
    last_completed=today.replace(day=1)-timedelta(days=1)
    year=last_completed.year if year is None else year
    month=(last_completed.month if year==last_completed.year else 12) if month is None else month
    if not 1900<=year<=2200 or not 1<=month<=12:raise HTTPException(400,'Invalid period')
    if (year,month)>(last_completed.year,last_completed.month):
        return jump('/?year='+str(last_completed.year)+'&month='+str(last_completed.month))
    completed=last_completed.month if year==last_completed.year else 12
    s=store()
    try:
        data=get_monthly(s,year,month);all_months=[get_monthly(s,year,m) for m in range(1,completed+1)]
        annual=year_reconciliation(s,year,as_of=today)
        # Dashboard is strictly actual cash-flow reporting. Allocation columns remain
        # available in core for other analytical reports, never for Dashboard display.
        # Explicit cash field is used to prevent accidental reintroduction of /12.
        def actual(d):
            return {**d, 'expenses': d['actual_cash_expenses'],
                    'income': d['actual_cash_income'],
                    'unregistered': d['opening']-d['closing']+d['actual_cash_income']-d['actual_cash_expenses']}
        data=actual(data)
        all_months=[actual(d) for d in all_months]
        columns=('opening','closing','income','expenses','unregistered')
        totals={key:sum(d[key] for d in all_months) for key in columns}
        averages={key:round(totals[key]/completed) if completed else 0 for key in columns}
        return html(req,'dashboard.html',year=year,month=month,data=data,months=all_months,years=selectable_years(s),
          annual=annual,totals=totals,averages=averages,completed_months=completed,
          **extras(s,'Dashboard'))
    finally:s.close()

@app.get('/details/{kind}',response_class=HTMLResponse)
def details(req:Request,kind:str,year:int,month:int):
    require(req)
    if kind not in ('income','expenses','unregistered'):raise HTTPException(404)
    if not 1900<=year<=2200 or not 1<=month<=12:raise HTTPException(400,'Invalid period')
    last_completed=date.today().replace(day=1)-timedelta(days=1)
    if (year,month)>(last_completed.year,last_completed.month):raise HTTPException(400,'Only completed months can be shown')
    s=store()
    try:
        d=get_monthly(s,year,month);lo=f'{year}-{month:02d}-01'
        from calendar import monthrange
        hi=f'{year}-{month:02d}-{monthrange(year,month)[1]:02d}'
        rows=[]
        if kind in ('income','unregistered'):
            for r in s.query('''SELECT i.tx_date, i.amount,c.name AS category,i.provider,i.notes,c.income_allocation
                  FROM incomes i LEFT JOIN categories c ON c.id=i.category_id
                  WHERE i.tx_date BETWEEN ? AND ? ORDER BY i.tx_date DESC''',(lo,hi)):
                rows.append((r['tx_date'],'Income / '+str(r['category'] or ''),r['amount'],r['notes'] or r['provider'] or ''))
        if kind in ('expenses','unregistered'):
            for r in s.query('''SELECT e.tx_date,e.amount,c.name category,e.notes FROM expenses e
                 LEFT JOIN categories c ON c.id=e.category_id WHERE e.tx_date BETWEEN ? AND ? ORDER BY e.tx_date DESC''',(lo,hi)):
                rows.append((r['tx_date'],'Expense / '+str(r['category'] or ''),r['amount'],r['notes'] or ''))
        inv=investment_cashflows(s,lo,hi)
        inv['records'].extend(debt_payment_cashflows(s,lo,hi)['records'])
        for r in inv['records']:
            if kind=='unregistered' or r['kind']==kind:
                rows.append((r['date'],('Debt / ' if r['action']=='Payment' else 'Investment / ')+r['action']+' / '+r['asset'],r['amount'],r['notes']))
        if kind=='unregistered':
            for dt,typ in [(d['opening_date'],'Opening snapshot'),(d['closing_date'],'Closing snapshot')]:
                for r in s.query('''SELECT s.amount,a.name FROM snapshots s JOIN accounts a ON a.id=s.account_id
                       WHERE s.snapshot_date=? ORDER BY a.name''',(dt,)):
                    rows.append((dt,typ+' / '+r['name'],r['amount'],''))
        return html(req,'details.html',rows=sorted(rows,key=lambda x:x[0],reverse=True),kind=kind,
          data=d,year=year,month=month,**extras(s,'Dashboard'))
    finally:s.close()

@app.get('/course',response_class=HTMLResponse)
def course(req:Request,year:int|None=None):
    require(req);year=year or date.today().year;s=store()
    try:
        rows=budget_rows(s,year)
        # Root expense totals are informational roll-ups and must not be summed twice.
        summary={kind:{'planned':sum(r['planned'] for r in rows if r['kind']==kind),
                       'actual':sum(r['actual'] for r in rows if r['kind']==kind),
                       'difference':sum(r['difference'] for r in rows if r['kind']==kind)}
                 for kind in ('income','expense')}
        main_rows=[r for r in rows if r['kind'] in ('income','expense')]
        expense_parent_rows=[r for r in rows if r['kind']=='expense total']
        return html(req,'course.html',year=year,rows=main_rows,expense_parent_rows=expense_parent_rows,
                    summary=summary,years=selectable_years(s),**extras(s,'Course','Budget vs Actual'))
    finally:s.close()

@app.get('/investments',response_class=HTMLResponse)
def investments(req:Request):
    require(req);s=store()
    try:
        from decimal import Decimal, ROUND_HALF_UP
        def fmt(value,digits):
            if value is None:return None
            step=Decimal(1).scaleb(-digits)
            return format(Decimal(value).quantize(step,rounding=ROUND_HALF_UP),f'.{digits}f')
        formatted=[]
        for item in portfolio(s):
            item=dict(item)
            for col in ('cost','value','realized','unrealized','distributions'):item[col]=fmt(item[col],2)
            item['units']=fmt(item['units'],6)
            formatted.append(item)
        return html(req,'portfolio.html',rows=formatted,**extras(s,'Investments','Portfolio'))
    finally:s.close()

@app.get('/debt',response_class=HTMLResponse)
def debt(req:Request):
    require(req);s=store()
    try:return html(req,'debt.html',rows=debt_schedule(s),**extras(s,'Debt','Debt schedule'))
    finally:s.close()

def filter_records(rows, field, search, filters):
    'Apply a case-insensitive text filter and AND-ed per-column Excel selections.'
    field = field or 'all'
    needle=(search or '').casefold()
    def include(record):
        cells=record['by_column']
        if needle:
            if field=='all':
                if not (needle in str(record['id']).casefold() or any(needle in str(v).casefold() for v in cells.values())):return False
            elif needle not in str(cells.get(field,'')).casefold():return False
        return all(str(cells.get(col,'')) in selected for col,selected in filters.items() if selected)
    return [r for r in rows if include(r)]


def parse_column_filters(params, columns):
    'GET values cf_<column>; ignore column names outside the current model.'
    return {col:set(params.getlist('cf_'+col)) for col in columns if params.getlist('cf_'+col)}


def generic_csv_rows(s, filename, table, model):
    'Parse generic CSV export with exact DB-column headers, without inserting.'
    columns=[col for col,_,_ in model]
    if table=='categories':columns+=['income_allocation','expense_allocation']
    with io.StringIO(filename.decode('utf-8-sig')) as f:
        reader=csv.DictReader(f,delimiter=';')
        header=[str(x).strip() for x in reader.fieldnames] if reader.fieldnames else []
        if table=='categories' and header==columns[:len(model)]:
            columns=columns[:len(model)]  # Also accept exports made by PanBank v4.0.3.
        elif table=='debt_payments' and header==[c for c in columns if c!='paid_by_other']:
            columns=header  # Backward-compatible CSV before the new checkbox column.
        elif header!=columns:
            raise ValueError('Unexpected CSV headers. Expected: '+';'.join(columns))
        output=[]
        for line,row in enumerate(reader,2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'CSV line {line}: invalid number of columns')
            if not any(str(v).strip() for v in row.values()):continue
            values={}
            for col,_,typ in model:
                raw=(row.get(col) or '0' if col=='paid_by_other' else row[col]).strip()
                if typ=='money':v=cents(raw or '0')
                elif typ=='date':v=iso(raw)
                elif typ in ('integer','year'):v=int(raw)
                elif typ=='bool':
                    if raw not in ('0','1'):raise ValueError(f'{col}: expected 0 or 1')
                    v=int(raw)
                elif typ in LOOKUPS:
                    v=raw if typ=='currency_code' else int(raw) if raw else None
                elif typ=='decimal':v=str(__import__('decimal').Decimal(raw or '0'))
                else:v=raw
                values[col]=v
            validate_record(s,table,values)
            if table=='categories' and 'income_allocation' in columns:
                for key in ('income_allocation','expense_allocation'):
                    if row[key] not in ('normal','annual_12'):
                        raise ValueError(f'CSV line {line}: invalid {key}')
                    values[key]=row[key]
                if values['kind']!='income' and values['income_allocation']!='normal':
                    raise ValueError(f'CSV line {line}: only incomes can use income allocation')
                # Legacy expense allocation flag is retained for CSV compatibility,
                # but is ignored by the expense reporting engine.
            output.append(tuple(values[c] for c in columns))
        return columns,output


def prepare_chart(s, year, chart):
    'Return labels and one/two monetary series in integer cents.'
    monthly=[get_monthly(s,year,m) for m in range(1,13)]
    months=[calendar.month_abbr[m] for m in range(1,13)]
    if chart=='flow':return months,[d['income'] for d in monthly],[d['expenses'] for d in monthly],'Income','Registered expenses'
    if chart=='unregistered':return months,[d['unregistered'] for d in monthly],None,'Unregistered expenses',''
    if chart=='balances':return months,[d['closing'] if d['closing_n'] else None for d in monthly],None,'Closing balances',''
    if chart=='annual_flow':
        years=list(range(year-4,year+1))
        def total(table,y):
            return s.scalar(f'SELECT COALESCE(SUM(amount),0) FROM {table} WHERE tx_date>=? AND tx_date<?',
                            (f'{y}-01-01',f'{y+1}-01-01'))
        return [str(y) for y in years],[total('incomes',y) for y in years], [total('expenses',y) for y in years],'Registered incomes','Registered expenses'
    if chart=='expense_roots':
        rows=s.query('''SELECT p.name, SUM(e.amount) AS total FROM expenses e
          JOIN categories c ON c.id=e.category_id JOIN categories p ON p.id=c.parent_id
          WHERE e.tx_date>=? AND e.tx_date<? GROUP BY p.id ORDER BY total DESC,p.name''',
          (f'{year}-01-01',f'{year+1}-01-01'))
        return [r['name'] for r in rows],[r['total'] for r in rows],None,'Registered expenses',''
    if chart=='budget_expenses':
        rows=s.query('''SELECT c.name, p.name AS parent, b.amount planned,
          COALESCE((SELECT SUM(e.amount) FROM expenses e WHERE e.category_id=c.id AND e.tx_date>=? AND e.tx_date<?),0) actual
          FROM budgets b JOIN categories c ON c.id=b.category_id LEFT JOIN categories p ON p.id=c.parent_id
          WHERE b.year=? AND b.kind='expense' ORDER BY actual DESC,c.name LIMIT 15''',
          (f'{year}-01-01',f'{year+1}-01-01',year))
        return [(r['parent']+' → ' if r['parent'] else '')+r['name'] for r in rows],[r['planned'] for r in rows],[r['actual'] for r in rows],'Planned','Actual'
    raise ValueError('Unknown chart type')

@app.get('/records/{name}',response_class=HTMLResponse)
def records(req:Request,name:str,q:str='',field:str='all',sort:str='',desc:int=1,view:str='50'):
    require(req);table,model=model_name(name);s=store()
    try:
        columns=[col for col,_,_ in model]
        safe_cols={'id'}|set(columns)
        field=field if field in safe_cols else 'all'
        sort=sort if sort in safe_cols else ('tx_date' if 'tx_date' in safe_cols else 'snapshot_date' if 'snapshot_date' in safe_cols else 'name' if 'name' in safe_cols else 'id')
        limited={'Income register','Expense register','Monthly snapshots','Investment transactions','Annual budgets'}
        if name not in limited: view='All'
        valid_views={'50','100','1-month','2-months','3-months','year','All'}
        if view not in valid_views:raise HTTPException(400,'Invalid view range')
        # Use SQL limits, not Python slicing of an already downloaded full table.
        # The date windows are relative to today; budgets use the selected calendar year.
        date_col={'Income register':'tx_date','Expense register':'tx_date',
                  'Monthly snapshots':'snapshot_date','Investment transactions':'tx_date',
                  'Annual budgets':'year'}.get(name)
        condition='';args=[]
        if view in ('1-month','2-months','3-months','year') and date_col and not (q.strip() or any(req.query_params.getlist('cf_'+col) for col in columns)):
            if view=='year':
                start=date(date.today().year,1,1)
                condition=f' WHERE {date_col}>=? AND {date_col}<?'
                args=[start.year,start.year+1] if name=='Annual budgets' else [start.isoformat(),date(start.year+1,1,1).isoformat()]
            else:
                offset=int(view[0]);now=date.today()
                y=now.year;m=now.month-offset
                while m<=0:y-=1;m+=12
                start=date(y,m,min(now.day,calendar.monthrange(y,m)[1]))
                if name=='Annual budgets':
                    condition=' WHERE year>=? AND year<=?';args=[start.year,now.year]
                else:
                    condition=f' WHERE {date_col}>=? AND {date_col}<=?';args=[start.isoformat(),now.isoformat()]
        default_sort=date_col or 'id'
        if view in ('50','100') and not (q.strip() or any(req.query_params.getlist('cf_'+col) for col in columns)):
            raw=s.query(f"SELECT id,{','.join(columns)} FROM {table} ORDER BY {default_sort} DESC,id DESC LIMIT ?",(int(view),))
            if sort!=default_sort or not desc:
                # Preserve explicit sorting within the selected recent-record window.
                raw=sorted(raw,key=lambda r:(r[sort] is None, r[sort]),reverse=bool(desc))
        else:
            raw=s.query(f"SELECT id,{','.join(columns)} FROM {table}{condition} ORDER BY {sort} {'DESC' if desc else 'ASC'},id DESC",tuple(args))
        all_rows=[]
        for r in raw:
            vals={col:rendered_val(s,r,col,typ) for col,_,typ in model}
            all_rows.append({'id':r['id'],'cells':list(vals.values()),'by_column':vals})
        filters=parse_column_filters(req.query_params,columns)
        # Distinct selectable column values, based on the text search, independent of the currently
        # checked values for that same column (allow adding more than one value).
        without_values=filter_records(all_rows,field,q,{})
        distinct={col:sorted({r['by_column'][col] for r in without_values},key=str.casefold) for col in columns}
        visible=filter_records(all_rows,field,q,filters)
        root=next((group for group,sub in GROUPS.items() if name in sub),'Settings')
        return html(req,'records.html',name=name,table=table,model=model,rows=visible,q=q,field=field,sort=sort,desc=desc,
            distinct=distinct,selected_filters=filters,filter_count=sum(len(v) for v in filters.values()),view=view,limited=name in limited,scanned_count=len(raw),search_all=bool(q.strip() or filters),
            **extras(s,root,name))
    finally:s.close()

@app.get('/edit/{name}',response_class=HTMLResponse)
@app.get('/edit/{name}/{id}',response_class=HTMLResponse)
def edit(req:Request,name:str,id:int|None=None):
    require(req);table,model=model_name(name);s=store()
    try:
        old=s.query(f'SELECT * FROM {table} WHERE id=?',(id,))[0] if id else None
        fields=[]
        for key,label,typ in model:
            if typ=='money':current=keyboard_money_text(old[key]) if old else ''
            else:current=old[key] if old and old[key] is not None else ''
            if typ=='date':current=european_date(current)
            fields.append(dict(key=key,label=label,typ=typ,value=current,options=choices(s,typ)))
        if name=='Categories':
            fields.append(dict(key='income_allocation',label='Income allocation',typ='income_allocation',
               value=old['income_allocation'] if old else 'normal',options=[('normal','Normal / transaction month'),('annual_12','Annual / spread across 12 months')]))
        cat_meta={str(r['id']):{'kind':r['kind'],'parent':r['parent_id']} for r in s.query('SELECT id,kind,parent_id FROM categories')}
        expense_groups=[(r['id'],r['name']) for r in s.query("SELECT id,name FROM categories WHERE kind='expense' AND parent_id IS NULL AND active=1 ORDER BY name COLLATE NOCASE")]
        selected_parent=None
        if name=='Expense register' and old and old['category_id']:
            selected_parent=cat_meta.get(str(old['category_id']),{}).get('parent')
        root=next((group for group,sub in GROUPS.items() if name in sub),'Settings')
        return html(req,'edit.html',name=name,id=id,fields=fields,category_meta=cat_meta,
            expense_groups=expense_groups,selected_parent=selected_parent,years=selectable_years(s),popup=req.query_params.get('popup')=='1',**extras(s,root,name))
    finally:s.close()

@app.post('/edit/{name}')
@app.post('/edit/{name}/{id}')
async def edit_submit(req:Request,name:str,id:int|None=None):
    require(req);table,model=model_name(name)
    data=await req.form();require_csrf(req,data.get('csrf'))
    s=store()
    try:
        values={}
        for key,label,typ in model:
            raw=str(data.get(key,'')).strip()
            if typ=='money':value=keyboard_cents(raw or '0')
            elif typ in LOOKUPS:
                value=raw if typ=='currency_code' else int(raw) if raw else None
            elif typ=='bool':value=1 if raw=='1' else 0
            elif typ in ('integer','year'):value=int(raw)
            elif typ=='date':value=iso(raw)
            elif typ=='decimal':value=str(__import__('decimal').Decimal(raw or '0'))
            else:value=raw
            values[key]=value
        validate_record(s,table,values,id)
        if name=='Categories':
            v=data.get('income_allocation','normal')
            if values['kind']!='income':v='normal'
            if v not in ('normal','annual_12'):raise ValueError('Invalid allocation')
            values['income_allocation']=v
            # Legacy SQLite field retained, but expense allocation is mandatory.
            values['expense_allocation']='normal'
        cols=list(values)
        if id:
            s.run(f"UPDATE {table} SET "+','.join(x+'=?' for x in cols)+' WHERE id=?',tuple(values.values())+(id,))
        else:
            s.run(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(values.values()))
        if req.query_params.get('popup')=='1':
            # Popup originated on same origin; refresh the opener, then close.
            return HTMLResponse('<!doctype html><meta charset="utf-8"><script>'
                'if(window.opener&&!window.opener.closed){window.opener.location.reload();}'
                'window.close();</script><p>Saved. You may close this window.</p>')
        return jump('/records/'+quote(name))
    except (ValueError,sqlite3.Error) as exc:
        raise HTTPException(400,detail=str(exc))
    finally:s.close()

@app.post('/delete-selected/{name}')
async def delete_selected(req:Request,name:str):
    require(req);table,_=model_name(name)
    data=await req.form();require_csrf(req,data.get('csrf'))
    if data.get('confirm_first')!='YES' or data.get('confirm_second')!='DELETE':
        raise HTTPException(400,'Two explicit confirmations are required')
    raw=data.getlist('ids')
    if not raw or len(raw)>10000:raise HTTPException(400,'Select 1 to 10,000 records')
    try:ids=sorted({int(v) for v in raw})
    except ValueError:raise HTTPException(400,'Invalid record ID')
    if any(v<=0 for v in ids):raise HTTPException(400,'Invalid record ID')
    s=store()
    try:
        with s.db:
            count=s.db.execute(f'SELECT COUNT(*) FROM {table} WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchone()[0]
            if count!=len(ids):raise ValueError('Selected records changed; refresh and retry')
            s.db.executemany(f'DELETE FROM {table} WHERE id=?',[(id,) for id in ids])
        if req.query_params.get('popup')=='1':
            # Popup originated on same origin; refresh the opener, then close.
            return HTMLResponse('<!doctype html><meta charset="utf-8"><script>'
                'if(window.opener&&!window.opener.closed){window.opener.location.reload();}'
                'window.close();</script><p>Saved. You may close this window.</p>')
        return jump('/records/'+quote(name))
    except (ValueError,sqlite3.Error) as exc:raise HTTPException(400,detail='Nothing deleted: '+str(exc))
    finally:s.close()

@app.post('/delete/{name}/{id}')
async def delete(req:Request,name:str,id:int):
    require(req);table,_=model_name(name);data=await req.form();require_csrf(req,data.get('csrf'))
    if data.get('confirmed')!='DELETE':raise HTTPException(400,'Explicit deletion confirmation required')
    s=store()
    try:s.run(f'DELETE FROM {table} WHERE id=?',(id,))
    finally:s.close()
    return jump('/records/'+quote(name))

@app.get('/search',response_class=HTMLResponse)
def search(req:Request,q:str='',amount:str='',kind:str='all'):
    require(req);s=store()
    try:
        results=[]
        if q or amount:
            parsed=keyboard_cents(amount) if amount else None
            for name,(table,model) in MODELS.items():
                if kind!='all' and kind!=name:continue
                rows=s.query(f"SELECT id,{','.join(x[0] for x in model)} FROM {table} ORDER BY id DESC LIMIT 10000")
                for r in rows:
                    cells=[rendered_val(s,r,c,t) for c,_,t in model]
                    if (not q or q.casefold() in name.casefold() or any(q.casefold() in str(v).casefold() for v in cells)) and (parsed is None or any(r[c]==parsed for c,_,t in model if t=='money')):
                        results.append((name,r['id'],' | '.join(cells)))
                        if len(results)>=1500:break
                if len(results)>=1500:break
        return html(req,'search.html',rows=results,q=q,amount=amount,kind=kind,names=list(MODELS),**extras(s,'Global Search'))
    finally:s.close()

@app.get('/charts',response_class=HTMLResponse)
def charts(req:Request,year:int|None=None,chart:str='flow'):
    require(req);year=year or date.today().year
    if not 1900<=year<=2200:raise HTTPException(400,'Invalid year')
    s=store()
    try:
        try:labels,a,b,legend1,legend2=prepare_chart(s,year,chart)
        except ValueError:raise HTTPException(400,'Unknown chart')
        return html(req,'charts.html',year=year,years=selectable_years(s),chart=chart,labels=labels,series=a,series2=b,legend1=legend1,legend2=legend2,
           **extras(s,'Charts'))
    finally:s.close()

# Database management: only configured server-local directories are browsable.
# The server does NOT accept browser-uploaded database files, arbitrary filesystem paths,
# or immediate hot-swapping of active database connections.
def allowed_db_roots():
    roots = [APP_DIR, Path(os.getenv('PANBANK_HOME') or SETTINGS.get('database_directory') or str(APP_DIR))]
    roots.extend(Path(item) for item in os.getenv('PANBANK_DB_ALLOWED_DIRS', '').split(os.pathsep) if item.strip())
    result = []
    for path in roots:
        p = path.expanduser().resolve()
        if p.is_dir() and p not in result:
            result.append(p)
    return result

def validate_db_candidate(raw_path):
    path = Path(raw_path).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in allowed_db_roots()):
        raise ValueError('Database must be inside an allowed server directory. Configure PANBANK_DB_ALLOWED_DIRS and restart if needed.')
    if not path.is_file() or path.suffix.lower() not in ('.sqlite3', '.sqlite', '.db'):
        raise ValueError('Choose an existing SQLite database file (.sqlite3, .sqlite, or .db).')
    if path.stat().st_size > 4 * 1024**3:
        raise ValueError('Database exceeds the configured 4 GiB validation limit.')
    # Validate read-only; do not create/modify files or run migrations during selection.
    try:
        with path.open('rb') as file:
            if file.read(16) != b'SQLite format 3\x00':
                raise ValueError('Selected file does not have a valid SQLite header.')
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            connection.execute('PRAGMA query_only=ON')
            ok = connection.execute('PRAGMA quick_check(1)').fetchone()[0]
            if ok != 'ok':
                raise ValueError('SQLite integrity check failed: '+str(ok))
            names = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required = {'categories','accounts','incomes','expenses','snapshots','budgets','investments','debts'}
            missing = required - names
            if missing:
                raise ValueError('Not a recognized PanBank database; missing tables: '+', '.join(sorted(missing)))
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError('Could not read SQLite database: '+str(exc)) from exc
    return path

def server_database_options():
    files = set()
    for root in allowed_db_roots():
        # Only immediate files; no recursive scan of private server data.
        try:
            files.update(p.resolve() for p in root.iterdir()
                         if p.is_file() and p.suffix.lower() in ('.sqlite3','.sqlite','.db'))
        except OSError:
            continue
    return sorted(files, key=lambda p: str(p).casefold())

@app.get('/settings/database', response_class=HTMLResponse)
def database_management(req:Request):
    require(req)
    configured = read_app_settings().get('database_path')
    if not configured and CONFIG_FILE.is_file():
        try:configured=json.loads(CONFIG_FILE.read_text(encoding='utf-8')).get('database_path')
        except (ValueError,OSError):configured='Invalid legacy configuration'
    return html(req,'database_management.html',
        configured=configured, options=server_database_options(),
        roots=allowed_db_roots(), pending=bool(configured and str(DB_PATH)!=str(configured)),
        **extras(None,'Settings','Database Management'))

@app.post('/settings/database')
async def database_management_post(req:Request):
    data=await req.form()
    require_csrf(req,data.get('csrf'))
    if data.get('confirmation') != 'SWITCH':
        raise HTTPException(400, 'Type SWITCH to confirm the database selection.')
    target=validate_db_candidate(str(data.get('database_path','')).strip())
    if target == DB_PATH:
        return jump('/settings/database')
    # Store database selection alongside the login hash and home directory.
    updated = dict(read_app_settings())
    updated['database_path'] = str(target)
    try:
        save_app_settings(updated)
        CONFIG_FILE.unlink(missing_ok=True)   # Retire old v4.0.1 selection only after saving.
    except (OSError,ValueError) as exc:
        raise HTTPException(500,detail='Could not save database selection: '+str(exc)) from exc
    return jump('/settings/database')

@app.post('/settings/database/default')
async def database_default(req:Request):
    data=await req.form()
    require_csrf(req,data.get('csrf'))
    if data.get('confirmation')!='DEFAULT':
        raise HTTPException(400,'Type DEFAULT to confirm.')
    updated = dict(read_app_settings())
    updated.pop('database_path',None)
    save_app_settings(updated)
    CONFIG_FILE.unlink(missing_ok=True)
    return jump('/settings/database')

@app.get('/tools',response_class=HTMLResponse)
def tools(req:Request):
    require(req);s=store()
    try:return html(req,'tools.html',**extras(s,'Data & Backups'))
    finally:s.close()

@app.post('/backup')
def backup(req:Request,csrf:str=Form(...)):
    require_csrf(req,csrf);s=store()
    try:
        stream=io.BytesIO();tmp=APP_DIR/'_panbank_backup_temp.sqlite3'
        if tmp.exists():raise HTTPException(409,'Backup already in progress')
        try:
            s.backup(tmp);payload=tmp.read_bytes()
        finally:tmp.unlink(missing_ok=True)
        return Response(payload,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="panbank-backup.sqlite3"','Cache-Control':'no-store'})
    finally:s.close()

@app.get('/export/{name}')
def export_csv(req:Request,name:str):
    require(req);table,model=model_name(name);s=store()
    try:
        output=io.StringIO();writer=csv.writer(output,delimiter=';')
        cols=[col for col,_,_ in model]
        if table=='categories':cols+=['income_allocation','expense_allocation']
        writer.writerow(cols)
        for r in s.query(f"SELECT {','.join(cols)} FROM {table} ORDER BY id"):
            writer.writerow([money_text(r[c]) if typ=='money' else r[c] for c in cols for typ in [next((t for k,_,t in model if k==c),'text')]])
        content='\ufeff'+output.getvalue()
        return Response(content,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{table}.csv"'})
    finally:s.close()

@app.post('/import/{name}')
async def import_csv(req:Request,name:str,csrf:str=Form(...),file:UploadFile=File(...)):
    require_csrf(req,csrf)
    table,model=model_name(name)
    content=await file.read()
    if len(content)>10_000_000:raise HTTPException(413,'Maximum CSV size is 10 MB')
    import tempfile
    temp=None;s=store()
    try:
        with tempfile.NamedTemporaryFile(mode='wb',suffix='.csv',delete=False) as f:
            f.write(content);temp=f.name
        if table in ('incomes','expenses'):
            fields,rows=read_transaction_csv(s,temp,table)
        else:
            fields,rows=generic_csv_rows(s,content,table,model)
        if not rows:raise ValueError('No non-empty CSV rows to import')
        with s.db:
            s.db.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",rows)
        return jump('/records/'+quote(name))
    except (UnicodeError,ValueError,sqlite3.Error) as exc:raise HTTPException(400,detail=str(exc))
    finally:
        if temp:Path(temp).unlink(missing_ok=True)
        s.close()

# Authenticated tab PDF export. Render source views using their existing templates,
# extract actual visible tables and labels, and create a multi-page printable PDF.
@app.get('/pdf')
def export_pdf(req:Request,target:str='/'):
    require(req)
    from urllib.parse import urlsplit,parse_qs
    from html import escape
    from bs4 import BeautifulSoup
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    parsed=urlsplit(target)
    path=parsed.path
    if path in ('/pdf','/tools','/settings/database','/login','/logout','/backup') or parsed.scheme or parsed.netloc:
        raise HTTPException(400,'PDF export unavailable for this page')
    # Preserve the original view's query parameters for filters, sorting and dates.
    view_req=Request({**req.scope,'path':path,'raw_path':path.encode('utf-8'),
                      'query_string':parsed.query.encode('utf-8')})
    params=parse_qs(parsed.query,keep_blank_values=True)
    def val(key,default=None):return params.get(key,[default])[0]
    def num(key,default=None):
        v=val(key)
        try:return int(v) if v is not None else default
        except ValueError:raise HTTPException(400,'Invalid numeric query')
    def record_name(prefix):
        name=__import__('urllib.parse',fromlist=['unquote']).unquote(path[len(prefix):])
        model_name(name)
        return name
    chart_pdf_data=None
    if path=='/': response=dashboard(view_req,num('year'),num('month'))
    elif path=='/course':response=course(view_req,num('year'))
    elif path=='/investments':response=investments(view_req)
    elif path=='/debt':response=debt(view_req)
    elif path=='/search':response=search(view_req,val('q',''),val('amount',''),val('kind','all'))
    elif path=='/charts':
        yr=num('year',date.today().year);which=val('chart','flow');ss=store()
        try:
            labels,a,b,legend1,legend2=prepare_chart(ss,yr,which)
            chart_pdf_data=(labels,a,b,legend1,legend2,which)
        finally:ss.close()
        table_head=['Period',legend1]+([legend2] if b is not None else [])
        table_rows=[[str(label),eur(a[i]) if a[i] is not None else 'Missing snapshot']+([eur(b[i]) if b[i] is not None else 'Missing snapshot'] if b is not None else []) for i,label in enumerate(labels)]
        response=HTMLResponse('<h1>Financial chart: '+escape(which)+' ('+str(yr)+')</h1><table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in table_head)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(x)+'</td>' for x in row)+'</tr>' for row in table_rows)+'</table>')
    elif path.startswith('/records/'):
        response=records(view_req,record_name('/records/'),val('q',''),val('field','all'),val('sort',''),num('desc',1),val('view','50'))
    elif path.startswith('/details/'):
        response=details(view_req,path[len('/details/'):],num('year',date.today().year),num('month',1))
    elif path.startswith('/edit/'):
        seg=path[len('/edit/'):].rsplit('/',1)
        if len(seg)==2 and seg[1].isdigit():response=edit(view_req,__import__('urllib.parse',fromlist=['unquote']).unquote(seg[0]),int(seg[1]))
        else:response=edit(view_req,record_name('/edit/'))
    else:raise HTTPException(404,'No printable tab at this address')
    if response.status_code!=200:raise HTTPException(response.status_code,'View cannot be exported')
    soup=BeautifulSoup(response.body,'html.parser')
    # Snapshot the rendered report tables BEFORE removing HTML forms. Register
    # tables are nested inside the bulk-delete form; extraction must never depend
    # on later HTML sanitation or how browsers parse those forms.
    main=soup.find('main') or soup
    register_tables=[]
    if path.startswith('/records/'):
        register_tables=[BeautifulSoup(str(t),'html.parser').find('table')
                         for t in main.find_all('table')]
        if not register_tables:
            raise HTTPException(500,'The register view has no printable table')
    # Printable only: never expose hidden inputs, session data, or administrative navigation.
    # Keep data tables even when an interactive form wraps them (e.g. the
    # bulk-delete form on every register). Removing all <form> tags with
    # decompose() destroyed the register rows and produced heading-only PDFs.
    for tag in main.select('nav,aside,script,style,button,footer,.subnav,header,details,input,select,textarea'):
        tag.decompose()
    for form in list(main.find_all('form')):
        if form.find('table'):
            form.unwrap()  # Remove just the form element; preserve its table.
        else:
            form.decompose()
    font_path=next((font for font in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/segoeui.ttf'] if Path(font).is_file()),'')
    if Path(font_path).is_file() and 'PanBankPDF' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('PanBankPDF',font_path))
    font='PanBankPDF' if 'PanBankPDF' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
    styles=getSampleStyleSheet()
    body=ParagraphStyle('PanBody',parent=styles['BodyText'],fontName=font,fontSize=8,leading=11,spaceAfter=7)
    head=ParagraphStyle('PanHead',parent=body,fontSize=14,leading=18,spaceAfter=12)
    cell=ParagraphStyle('PanCell',parent=body,fontSize=6.3,leading=9,spaceAfter=0,wordWrap='CJK')
    elements=[Paragraph('PanBank — '+escape(path),head)]
    elements.append(Paragraph('Generated '+date.today().isoformat()+' · '+escape(target),body))
    if chart_pdf_data:
        from reportlab.graphics.shapes import Drawing,String
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        labels,a,b,leg1,leg2,which=chart_pdf_data
        if 0<len(labels)<=24:
            if which=='budget_expenses':
                from reportlab.graphics.charts.barcharts import HorizontalBarChart
                drawing=Drawing(730,max(260,32*len(labels)+55))
                graph=HorizontalBarChart()
                graph.x=270;graph.y=30;graph.width=420;graph.height=drawing.height-60
            else:
                drawing=Drawing(730,240)
                graph=VerticalBarChart()
                graph.x=48;graph.y=50;graph.height=150;graph.width=660
            # Missing balances are absent data, not zero-valued snapshots. The PDF
            # table preserves that distinction; chart zeroes are placeholders only.
            chart_values=[v for v in (a+(b or [])) if v is not None]
            graph.data=[tuple((v or 0)/100 for v in a)]+([tuple((v or 0)/100 for v in b)] if b is not None else [])
            graph.categoryAxis.categoryNames=[str(v)[:42 if which=='budget_expenses' else 14] for v in labels]
            if which=='budget_expenses':
                graph.categoryAxis.labels.fontSize=7
                graph.categoryAxis.labels.boxAnchor='e'
            else:
                graph.categoryAxis.labels.angle=35
                graph.categoryAxis.labels.boxAnchor='ne'
                graph.categoryAxis.labels.fontSize=6
            graph.valueAxis.valueMin=min(0,min(chart_values or [0])/100)
            graph.valueAxis.valueMax=max(1,max(chart_values or [0])/100)
            graph.valueAxis.valueStep=max(1,(graph.valueAxis.valueMax-graph.valueAxis.valueMin)/5)
            drawing.add(graph)
            elements.extend([drawing,Spacer(1,12)])
    # For registers, use the independent snapshot instead of the potentially
    # mutated DOM. This works for all register models, including budgets and
    # investments, and preserves the existing record filters / view range.
    blocks=list(main.find_all(['h1','h2','p','table','div'],recursive=True))
    if register_tables:
        blocks=[block for block in blocks if block.name!='table']+register_tables
    for block in blocks:
        # Divisions are layout containers, not additional text copies.
        if block.name=='div':
            if 'metric' in (block.get('class') or []):
                # The dashboard metric number is in a sibling div to the label.
                # Print them together so the PDF does not contain bare amounts.
                parent=block.find_parent('div',class_='card')
                label=parent.find('div',class_='muted') if parent else None
                amount=block.get_text(' ',strip=True)
                name=label.get_text(' ',strip=True) if label else 'Amount'
                elements.append(Paragraph('<b>'+escape(name)+'</b>: '+escape(amount),body))
            continue
        if block.name in ('h1','h2'):
            elements.append(Paragraph(escape(block.get_text(' ',strip=True)),head if block.name=='h1' else body))
        elif block.name=='p':
            if block.find_parent('table'):continue
            txt=block.get_text(' ',strip=True)
            if txt:elements.append(Paragraph(escape(txt[:1500]),body))
        elif block.name=='table':
            matrix=[]
            trs=block.find_all('tr')
            for tr in trs[:10000]:
                cells=tr.find_all(['th','td'],recursive=False)
                if cells:matrix.append([Paragraph(escape(c.get_text(' ',strip=True)[:500]),cell) for c in cells])
            if not matrix:continue
            max_cols=max(len(row) for row in matrix)
            matrix=[row+[Paragraph('',cell)]*(max_cols-len(row)) for row in matrix]
            width=landscape(A4)[0]-64
            widths=[width/max_cols]*max_cols
            table=LongTable(matrix,colWidths=widths,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dfe9f2')),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),0.5,colors.gray),
                ('BOTTOMPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5)]))
            elements.extend([table,Spacer(1,10)])
    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=landscape(A4),leftMargin=32,rightMargin=32,topMargin=32,bottomMargin=32)
    doc.build(elements)
    return Response(buffer.getvalue(),media_type='application/pdf',headers={
        'Content-Disposition':'attachment; filename="PanBank-'+date.today().isoformat()+'.pdf"',
        'Cache-Control':'no-store'})

if __name__=='__main__':
    if SETUP_MODE:
        first_setup()
        raise SystemExit(0)
    import uvicorn
    host=os.environ.get('PANBANK_HOST','127.0.0.1')
    port=int(os.environ.get('PANBANK_PORT','8765'))
    uvicorn.run(app,host=host,port=port,proxy_headers=False)
