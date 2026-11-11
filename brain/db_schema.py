"""Atomic v0 adoption and v1 validation for the small operational stores.

Only a fresh database or the exact known legacy layout may be adopted. A future
version or drift fails before startup recovery touches delivery/cost records.
"""
import sqlite3
from contextlib import closing
import re

class SchemaError(RuntimeError):
    pass

def _layout(db):
    result = {}
    for name, kind, sql in db.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"):
        if kind == 'table':
            definition = re.sub(r'\s+', '', re.sub(r'\bIF\s+NOT\s+EXISTS\b', '', sql, flags=re.I)).lower()
            result[name] = ('table', definition, tuple(tuple(row)[1:] for row in db.execute('PRAGMA table_xinfo("' + name.replace('"', '""') + '")')))
        elif kind == 'index':
            # Include uniqueness, order, collation and the partial-index predicate.
            table = db.execute('SELECT tbl_name FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
            flags = next(tuple(row)[2:] for row in db.execute('PRAGMA index_list("' + table.replace('"', '""') + '")') if row[1] == name)
            predicate = re.split(r'\bwhere\b', sql or '', flags=re.I)[1:]
            result[name] = ('index', flags, tuple(tuple(row)[1:] for row in db.execute('PRAGMA index_xinfo("' + name.replace('"', '""') + '")')), tuple(re.sub(r'\s+', '', x).lower() for x in predicate))
        else:
            result[name] = (kind, sql)
    # Autoindexes enforce primary/unique keys and must also match.
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        result['@' + table] = tuple(sorted((tuple(row)[2:], tuple(tuple(x)[1:] for x in db.execute('PRAGMA index_xinfo("' + row[1].replace('"', '""') + '")'))) for row in db.execute('PRAGMA index_list("' + table.replace('"', '""') + '")')))
    return result

def ensure_v1(db, statements, label):
    with closing(sqlite3.connect(':memory:')) as reference:
        for statement in statements:
            reference.execute(statement)
        expected = _layout(reference)
    db.execute('BEGIN IMMEDIATE')
    try:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise SchemaError(f'{label}: unsupported schema version {version}')
        actual = _layout(db)
        if not actual and version == 0:
            for statement in statements:
                db.execute(statement)
        elif actual != expected:
            raise SchemaError(f'{label}: schema does not match known version {version}')
        if _layout(db) != expected:
            raise SchemaError(f'{label}: migration validation failed')
        if version == 0:
            db.execute('PRAGMA user_version=1')
        db.commit()
    except BaseException:
        db.rollback()
        raise
