"""Schema adoption must preserve dedupe, scheduled work and billing history."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from db_schema import ensure_v1, SchemaError
from jobs import Jobs
from telegram_transport import TelegramTransport
from cost_telemetry.store import Ledger, COST_SCHEMA

SCHEMA = ('CREATE TABLE test(id TEXT PRIMARY KEY,value INTEGER)',)

class OperationalSchema(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'db'
    def tearDown(self):
        self.temp.cleanup()
    def test_fresh_and_repeat(self):
        with sqlite3.connect(self.path) as db:
            ensure_v1(db, SCHEMA, 'test')
            db.execute("insert into test values('keep',42)");db.commit()
            ensure_v1(db, SCHEMA, 'test')
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],1)
            self.assertEqual(db.execute('select * from test').fetchall(),[('keep',42)])
    def test_future_version_never_creates_or_recovers(self):
        with sqlite3.connect(self.path) as db:
            db.execute('pragma user_version=2')
            with self.assertRaises(SchemaError):ensure_v1(db, SCHEMA, 'test')
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],2)
            self.assertEqual(db.execute('select name from sqlite_master').fetchall(),[])
    def test_wrong_column_constraint_or_extra_table_rejected(self):
        for ddl in ('CREATE TABLE test(id TEXT,value INTEGER)',
                    'CREATE TABLE test(id TEXT PRIMARY KEY,value TEXT)',
                    'CREATE TABLE other(id TEXT PRIMARY KEY)'):
            with sqlite3.connect(':memory:') as db:
                db.execute(ddl)
                before=db.execute('select sql from sqlite_master').fetchall()
                with self.assertRaises(SchemaError):ensure_v1(db, SCHEMA, 'test')
                self.assertEqual(db.execute('pragma user_version').fetchone()[0],0)
                self.assertEqual(db.execute('select sql from sqlite_master').fetchall(),before)
    def test_ddl_failure_rolls_back_all_changes(self):
        # Inject a disk/DDL failure after the first table on the target, not reference.
        class Failing(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith('CREATE TABLE second'):raise sqlite3.OperationalError('injected failure')
                return super().execute(sql,*args)
        with sqlite3.connect(self.path,factory=Failing) as db:
            with self.assertRaises(sqlite3.OperationalError):ensure_v1(db,SCHEMA+('CREATE TABLE second(id INTEGER)',),'test')
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],0)
            self.assertEqual(db.execute('select name from sqlite_master').fetchall(),[])
    def test_partial_legacy_costs_rejected_without_creating_rest(self):
        with sqlite3.connect(self.path) as db:
            db.execute(COST_SCHEMA[0]);before=db.execute('select sql from sqlite_master').fetchall()
            with self.assertRaises(SchemaError):Ledger(self.path)
            self.assertEqual(db.execute('select sql from sqlite_master').fetchall(),before)
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],0)
    def test_jobs_legacy_pending_preserved_and_delivery_not_replayed(self):
        j=Jobs(self.path,None)
        a=j.add(123,'timer','keep');b=j.add(456,'alarm','interrupted')
        j.db.execute("update jobs set status='delivering' where id=?",(b,));j.db.execute('pragma user_version=0');j.db.commit();j.close()
        j=Jobs(self.path,None)
        self.assertEqual(j.db.execute('pragma user_version').fetchone()[0],1)
        rows={x['id']:x for x in j.list()}
        self.assertEqual(rows[a]['status'],'pending');self.assertEqual(rows[a]['due'],123)
        self.assertEqual(rows[b]['status'],'unknown');j.close()
    def test_telegram_offset_receipt_and_dedupe_preserved(self):
        runtime=SimpleNamespace(config=SimpleNamespace(telegram_users={}))
        t=TelegramTransport(runtime,self.path,api=object())
        with t.db:
            t.db.execute('update meta set value=1000')
            t.db.execute("insert into updates values(999,'sent','chat','text','reply',555)")
            t.db.execute("insert into updates values(998,'sending','chat','text','reply',NULL)")
            t.db.execute('pragma user_version=0')
        t.db.close();t=TelegramTransport(runtime,self.path,api=object())
        self.assertEqual(t.offset,1000)
        self.assertIsNone(t.claim({'update_id':999}))
        self.assertEqual(t.db.execute('select status,message_id from updates where id=999').fetchone(),('sent',555))
        self.assertEqual(t.db.execute('select status from updates where id=998').fetchone()[0],'unknown');t.db.close()
    def test_telegram_failure_releases_single_owner_lock(self):
        with sqlite3.connect(self.path) as db:db.execute('pragma user_version=99')
        runtime=SimpleNamespace(config=SimpleNamespace(telegram_bot_token='test-token'))
        closed=[]
        class Lock:
            def close(self):closed.append(True)
        def acquire(owner):owner.lock=Lock()
        with patch.object(TelegramTransport,'acquire',acquire):
            with self.assertRaises(SchemaError):TelegramTransport(runtime,self.path)
        self.assertEqual(closed,[True])
    def test_costs_start_time_finished_cost_and_dedupe_preserved(self):
        l=Ledger(self.path,clock=lambda:100)
        cid=l.begin('proactivity','attention','openai','test')
        with l.db:
            l.db.execute("update calls set status='completed',response_id='receipt',cost_usd=.2 where id=?",(cid,))
            l.db.execute('pragma user_version=0')
        l.db.close();l=Ledger(self.path,clock=lambda:200)
        self.assertEqual(l.meta('started_at'),100)
        self.assertEqual(l.db.execute('select status,cost_usd from calls').fetchone()[:],('completed',.2))
        with self.assertRaises(sqlite3.IntegrityError):
            with l.db:l.db.execute("insert into calls select 'duplicate',started,finished,category,subcategory,request_ref,provider,model,tier,response_id,status,usage_known,input_tokens,output_tokens,cached_tokens,write_tokens,cost_usd,pricing,latency_ms,error from calls")
        l.db.close()

class RemainingOperationalSchemas(unittest.TestCase):
    setUp=OperationalSchema.setUp
    tearDown=OperationalSchema.tearDown
    def test_compatibility_v0_preserves_receipts_and_accepted_queue(self):
        from compatibility import Compatibility
        c=Compatibility(None,self.path)
        with c.db:
            c.db.execute("insert into requests values('accepted','ask','accepted','{}',NULL)")
            c.db.execute("insert into requests values('interrupted','ask','processing','{}',NULL)")
            c.db.execute("insert into deliveries values(1,'telegram','saved','sent',123)")
            c.db.execute('pragma user_version=0')
        c.db.close();c=Compatibility(None,self.path)
        self.assertEqual(c.db.execute('pragma user_version').fetchone()[0],1)
        self.assertEqual(c.db.execute("select status from requests where id='accepted'").fetchone()[0],'accepted')
        self.assertEqual(c.db.execute("select status from requests where id='interrupted'").fetchone()[0],'unknown')
        self.assertEqual(c.db.execute('select status,message_id from deliveries').fetchone(),('sent',123));c.db.close()
    def test_fast_v0_preserves_pending_clarification_and_audit(self):
        from fast_mode import FastMode
        f=FastMode(self.path)
        with f.db:
            f.db.execute("insert into pending values('conversation',123,'saved command','[]')")
            f.db.execute("insert into attempts values('request','{}')")
            f.db.execute('pragma user_version=0')
        f.db.close();f=FastMode(self.path)
        self.assertEqual(f.db.execute('select * from pending').fetchall(),[('conversation',123.,'saved command','[]')])
        self.assertEqual(f.db.execute('select * from attempts').fetchall(),[('request','{}')]);f.db.close()
    def test_operational_log_v0_preserves_history_and_autoincrement(self):
        from memory import Memory
        m=Memory(self.path);m.log_message('user','preserved')
        m._conn.execute('pragma user_version=0');m._conn.commit();m._conn.close();m=Memory(self.path)
        m.log_message('assistant','new')
        self.assertEqual([tuple(r) for r in m._conn.execute('select id,role,content from conversation_log order by id')],[(1,'user','preserved'),(2,'assistant','new')]);m._conn.close()
    def test_all_remaining_stores_refuse_future_versions_before_recovery(self):
        from compatibility import Compatibility
        from fast_mode import FastMode
        from memory import Memory
        for make in (lambda:Compatibility(None,self.path),lambda:FastMode(self.path),lambda:Memory(self.path)):
            with sqlite3.connect(self.path) as db:db.execute('pragma user_version=9')
            with self.assertRaises(SchemaError):make()
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute('pragma user_version').fetchone()[0],9)
                self.assertEqual(db.execute('select name from sqlite_master').fetchall(),[])
    def test_hidden_check_constraint_drift_is_rejected(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE test(id TEXT PRIMARY KEY,value INTEGER CHECK(value>=0))')
            with self.assertRaises(SchemaError):ensure_v1(db,SCHEMA,'test')
            self.assertEqual(db.execute('pragma user_version').fetchone()[0],0)
