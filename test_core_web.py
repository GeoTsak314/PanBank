import tempfile,unittest
from pathlib import Path
from core import Store,migrate_web,get_monthly,year_reconciliation,budget_rows,keyboard_cents,cents
class TestFinance(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'test.sqlite3');migrate_web(self.s)
  self.s.run("INSERT INTO categories(kind,name,income_allocation) VALUES ('income','Annual bonus','annual_12')")
  self.s.run("INSERT INTO categories(kind,name) VALUES ('income','Salary')")
  self.s.run("INSERT INTO categories(kind,name) VALUES ('expense','Living')")
  self.s.run("INSERT INTO categories(kind,name,parent_id) VALUES ('expense','Groceries',3)")
  self.s.run("INSERT INTO accounts(name) VALUES ('Test Bank')")
  for dt,amt in [('2026-01-01',1000000),('2026-10-01',950000),('2027-01-01',910000)]:
   self.s.run('INSERT INTO snapshots(snapshot_date,account_id,amount) VALUES (?,?,?)',(dt,1,amt))
  self.s.run("INSERT INTO incomes(tx_date,amount,category_id) VALUES ('2026-04-06',120000,1)")
  self.s.run("INSERT INTO incomes(tx_date,amount,category_id) VALUES ('2026-09-06',200000,2)")
  self.s.run("INSERT INTO expenses(tx_date,amount,category_id) VALUES ('2026-09-10',10000,4)")
  self.s.run("INSERT INTO budgets(year,kind,category_id,amount) VALUES (2026,'income',2,250000)")
  self.s.run("INSERT INTO budgets(year,kind,category_id,amount) VALUES (2026,'expense',4,120000)")
 def tearDown(self):self.s.close();self.temp.cleanup()
 def test_annual_spread(self):
  self.assertEqual([get_monthly(self.s,2026,i)['allocated_income'] for i in range(1,13)],[10000]*12)
  self.assertEqual(get_monthly(self.s,2026,4)['actual_cash_income'],120000)
  self.assertEqual(get_monthly(self.s,2026,4)['income'],10000)
 def test_annual_boundary(self):
  rec=year_reconciliation(self.s,2026)
  self.assertTrue(rec['complete']);self.assertEqual(rec['start'],'2026-01-01')
  self.assertEqual(rec['end'],'2027-01-01')
  # 10k opening minus 9.1k closing, + 1200 bonus + 2000 salary - 100 groceries
  self.assertEqual(rec['unregistered'],1000000-910000+120000+200000-10000)
 def test_latest_boundary(self):
  self.s.run("DELETE FROM snapshots WHERE snapshot_date='2027-01-01'")
  rec=year_reconciliation(self.s,2026)
  self.assertEqual(rec['end'],'2026-10-01')
  self.assertEqual(rec['income'],90000+200000) # Jan-Sep 9 months of annual allocation
 def test_budget_directions(self):
  v=budget_rows(self.s,2026)
  inc=next(x for x in v if x['kind']=='income' and x['name']=='Salary')
  exp=next(x for x in v if x['kind']=='expense' and 'Groceries' in x['name'])
  self.assertEqual(inc['difference'],-50000)
  self.assertEqual(exp['difference'],110000)
 def test_formats(self):
  self.assertEqual(keyboard_cents('1345.50'),134550)
  self.assertEqual(cents('1.345,50'),134550)
if __name__=='__main__':unittest.main()
