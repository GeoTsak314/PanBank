# PanBank Web v4.0.3 — single web entrypoint (desktop + mobile) by G. Tsakalos


from __future__ import annotations
import csv, io, os, secrets, sqlite3, json, tempfile, hashlib, hmac, argparse, getpass, stat
from datetime import date
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from core import (Store,DB_PATH,CONFIG_FILE,SETTINGS_FILE,read_app_settings,MODELS,LOOKUPS,OPTIONS,keyboard_cents,keyboard_money_text,
    cents,money_text,eur,iso,validate_record,get_monthly,year_reconciliation,budget_rows,
    investment_cashflows,portfolio,debt_schedule,read_transaction_csv)


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

app=FastAPI(title='PanBank Web v4.0.3 (by G. Tsakalos)',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SessionMiddleware,secret_key=SECRET,same_site='strict',https_only=os.getenv('PANBANK_HTTPS','0')=='1',max_age=12*3600)
if os.getenv('PANBANK_ALLOWED_HOSTS'):
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[x.strip() for x in os.environ['PANBANK_ALLOWED_HOSTS'].split(',')])
templates=Jinja2Templates(directory=str(APP_DIR/'templates'))
templates.env.filters['eur']=eur
templates.env.filters['money']=money_text
templates.env.filters['keyboard_money']=keyboard_money_text

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
    require(req); today=date.today();year=year or today.year;month=month or today.month
    if not 1900<=year<=2200 or not 1<=month<=12:raise HTTPException(400,'Invalid period')
    s=store()
    try:
        data=get_monthly(s,year,month);all_months=[get_monthly(s,year,m) for m in range(1,13)]
        annual=year_reconciliation(s,year)
        total_income=sum(d['income'] for d in all_months)
        total_exp=sum(d['expenses'] for d in all_months)
        return html(req,'dashboard.html',year=year,month=month,data=data,months=all_months,
          annual=annual,annual_income=total_income,annual_expenses=total_exp,
          **extras(s,'Dashboard'))
    finally:s.close()

@app.get('/details/{kind}',response_class=HTMLResponse)
def details(req:Request,kind:str,year:int,month:int):
    require(req)
    if kind not in ('income','expenses','unregistered'):raise HTTPException(404)
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
        for r in inv['records']:
            if kind=='unregistered' or r['kind']==kind:
                rows.append((r['date'],'Investment / '+r['action']+' / '+r['asset'],r['amount'],r['notes']))
        if kind in ('expenses','unregistered'):
            rows.append(('', 'Annual expense allocation (this month)',d['allocated_expenses'], 'Monthly portion of all annual-marked expenses in this calendar year'))
        if kind in ('income','unregistered'):
            rows.append(('', 'Annual income allocation (this month)',d['allocated_income'], 'Monthly portion of all annual-marked incomes in this calendar year'))
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
    try:return html(req,'course.html',year=year,rows=budget_rows(s,year),**extras(s,'Course','Budget vs Actual'))
    finally:s.close()

@app.get('/investments',response_class=HTMLResponse)
def investments(req:Request):
    require(req);s=store()
    try:return html(req,'portfolio.html',rows=portfolio(s),**extras(s,'Investments','Portfolio'))
    finally:s.close()

@app.get('/debt',response_class=HTMLResponse)
def debt(req:Request):
    require(req);s=store()
    try:return html(req,'debt.html',rows=debt_schedule(s),**extras(s,'Debt','Debt schedule'))
    finally:s.close()

@app.get('/records/{name}',response_class=HTMLResponse)
def records(req:Request,name:str,q:str='',field:str='all',sort:str='',desc:int=1):
    require(req);table,model=model_name(name);s=store()
    try:
        safe_cols={'id'}|{f[0] for f in model}
        sort=sort if sort in safe_cols else ('tx_date' if 'tx_date' in safe_cols else 'snapshot_date' if 'snapshot_date' in safe_cols else 'name' if 'name' in safe_cols else 'id')
        rows=s.query(f"SELECT id,{','.join(x[0] for x in model)} FROM {table} ORDER BY {sort} {'DESC' if desc else 'ASC'},id DESC LIMIT 10000")
        values=[]
        for r in rows:
            display_values=[rendered_val(s,r,k,t) for k,_,t in model]
            if q and not (q.casefold() in str(r['id']).casefold() or any(q.casefold() in v.casefold() for v in display_values if field=='all') or any(q.casefold() in v.casefold() for (k,_,t),v in zip(model,display_values) if field==k)):
                continue
            values.append({'id':r['id'],'cells':display_values})
        root=next((group for group,sub in GROUPS.items() if name in sub),'Settings')
        return html(req,'records.html',name=name,table=table,model=model,rows=values,q=q,field=field,sort=sort,desc=desc,
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
            fields.append(dict(key=key,label=label,typ=typ,value=current,options=choices(s,typ)))
        if name=='Categories':
            fields.append(dict(key='income_allocation',label='Income allocation',typ='income_allocation',
               value=old['income_allocation'] if old else 'normal',options=[('normal','Normal / transaction month'),('annual_12','Annual / spread across 12 months')]))
            fields.append(dict(key='expense_allocation',label='Expense allocation (subcategories only)',typ='expense_allocation',
               value=old['expense_allocation'] if old else 'normal',options=[('normal','Normal / transaction month'),('annual_12','Annual / spread across 12 months')]))
        cat_meta={str(r['id']):{'kind':r['kind'],'parent':r['parent_id']} for r in s.query('SELECT id,kind,parent_id FROM categories')}
        expense_groups=[(r['id'],r['name']) for r in s.query("SELECT id,name FROM categories WHERE kind='expense' AND parent_id IS NULL AND active=1 ORDER BY name COLLATE NOCASE")]
        selected_parent=None
        if name=='Expense register' and old and old['category_id']:
            selected_parent=cat_meta.get(str(old['category_id']),{}).get('parent')
        root=next((group for group,sub in GROUPS.items() if name in sub),'Settings')
        return html(req,'edit.html',name=name,id=id,fields=fields,category_meta=cat_meta,
            expense_groups=expense_groups,selected_parent=selected_parent,**extras(s,root,name))
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
            ex=data.get('expense_allocation','normal')
            if ex not in ('normal','annual_12'):raise ValueError('Invalid expense allocation')
            if ex=='annual_12' and (values['kind']!='expense' or values['parent_id'] is None):
                raise ValueError('Only second-level expense categories can use annual allocation')
            values['expense_allocation']=ex if values['kind']=='expense' and values['parent_id'] is not None else 'normal'
        cols=list(values)
        if id:
            s.run(f"UPDATE {table} SET "+','.join(x+'=?' for x in cols)+' WHERE id=?',tuple(values.values())+(id,))
        else:
            s.run(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(values.values()))
        return jump('/records/'+quote(name))
    except (ValueError,sqlite3.Error) as exc:
        raise HTTPException(400,detail=str(exc))
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
    require(req);year=year or date.today().year;s=store()
    try:
        data=[get_monthly(s,year,m) for m in range(1,13)]
        series=([d['unregistered'] for d in data] if chart=='unregistered' else [d['closing'] for d in data] if chart=='balances' else [d['income'] for d in data])
        series2=[d['expenses'] for d in data] if chart=='flow' else None
        return html(req,'charts.html',year=year,chart=chart,series=series,series2=series2,**extras(s,'Charts'))
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
        output=io.StringIO();writer=csv.writer(output,delimiter=';');writer.writerow([x[0] for x in model])
        for r in s.query(f"SELECT {','.join(x[0] for x in model)} FROM {table} ORDER BY id"):
            writer.writerow([money_text(r[c]) if t=='money' else r[c] for c,_,t in model])
        content='\ufeff'+output.getvalue()
        return Response(content,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{table}.csv"'})
    finally:s.close()

@app.post('/import/{name}')
async def import_csv(req:Request,name:str,csrf:str=Form(...),file:UploadFile=File(...)):
    require_csrf(req,csrf)
    table,model=model_name(name)
    if table not in ('incomes','expenses'):raise HTTPException(400,'CSV template importer supports Income and Expense only')
    content=await file.read()
    if len(content)>10_000_000:raise HTTPException(413,'Maximum CSV size is 10 MB')
    import tempfile
    temp=None;s=store()
    try:
        with tempfile.NamedTemporaryFile(mode='wb',suffix='.csv',delete=False) as f:
            f.write(content);temp=f.name
        fields,rows=read_transaction_csv(s,temp,table)
        with s.db:
            s.db.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",rows)
        return jump('/records/'+quote(name))
    except (UnicodeError,ValueError,sqlite3.Error) as exc:raise HTTPException(400,detail=str(exc))
    finally:
        if temp:Path(temp).unlink(missing_ok=True)
        s.close()

if __name__=='__main__':
    if SETUP_MODE:
        first_setup()
        raise SystemExit(0)
    import uvicorn
    host=os.environ.get('PANBANK_HOST','127.0.0.1')
    port=int(os.environ.get('PANBANK_PORT','8765'))
    uvicorn.run(app,host=host,port=port,proxy_headers=False)
