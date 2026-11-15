import subprocess, sys, pathlib
A='rose/app_builder/migrations.py'
M=[
 ('attach allowed', "    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,\n                  sqlite3.SQLITE_SAVEPOINT) + TEMP_CREATES:", "    if action in (sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,\n                  sqlite3.SQLITE_SAVEPOINT) + TEMP_CREATES:"),
 ('temp creates allowed', "                  sqlite3.SQLITE_SAVEPOINT) + TEMP_CREATES:", "                  sqlite3.SQLITE_SAVEPOINT):"),
 ('any pragma', "        return sqlite3.SQLITE_OK if name in PRAGMAS or name in READ_PRAGMAS and arg2 is None else sqlite3.SQLITE_DENY", "        return sqlite3.SQLITE_OK"),
 ('read pragmas settable', " or name in READ_PRAGMAS and arg2 is None else", " or name in READ_PRAGMAS else"),
 ('alter db unchecked', "        return sqlite3.SQLITE_OK if arg1 == 'main' else sqlite3.SQLITE_DENY", "        return sqlite3.SQLITE_DENY"),
 ('temp catalog denied', "    if database == 'temp' and action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_UPDATE) \\\n            and arg1 in ('sqlite_temp_master', 'sqlite_temp_schema'):", "    if False:"),
 ('temp_store default', "        self.db.execute('PRAGMA temp_store = MEMORY')", "        pass"),
 ('verify unguarded', "        except sqlite3.Error as error:\n            raise self._refused(f'Checking the database after the {label}', error) from None", "        except ZeroDivisionError as error:\n            raise self._refused(f'Checking the database after the {label}', error) from None"),
 ('typed seed off', "                            if variant is None or not cols:", "                            if True:"),
 ('identical pair off', "                for rows in ((0, 0), (0, 1), None):", "                for rows in ((0, 1), None):"),
 ('nul ok', "                    if '\\x00' in sql:", "                    if False:"),
 ('splitter trigger state', "          (1, 4, 2, 2, 2, 4, 5, 2), (6, 5, 5, 5, 5, 5, 5, 5)", "          (1, 4, 2, 2, 2, 4, 2, 2), (6, 5, 5, 5, 5, 5, 5, 5)"),
 ('splitter unterminated ok', "    if state not in (0, 1):", "    if False:"),
 ('no lock', "    check_lock(current, delivered)\n", "\n"),
 ('rename ok', "        if (mine.name, mine.sha256) != (locked['name'], locked['sha256']):", "        if mine.sha256 != locked['sha256']:"),
 ('no seed', "            upgrade.seed()\n", "\n"),
 ('no step limit', "            return 1 if self.steps > MAX_STEPS else 0", "            return 0"),
]
for name, old, new in M:
    p=pathlib.Path(A); s=p.read_text(); assert s.count(old)==1, name
    p.write_text(s.replace(old,new))
    try:
        r=subprocess.run([sys.executable,'-m','unittest','rose.tests.test_app_migrations'],capture_output=True,text=True,timeout=240); rc=r.returncode
    except subprocess.TimeoutExpired: rc='timeout'
    finally:
        p.write_text(s)
    print(('KILLED ' if rc else 'SURVIVED ')+name, '' if rc in (0,1) else rc)
