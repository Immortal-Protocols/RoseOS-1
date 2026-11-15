import subprocess, sys, pathlib
A='rose/app_builder/migrations.py'
M=[
 ('attach allowed', "    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,\n                  sqlite3.SQLITE_SAVEPOINT):", "    if action in (sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,\n                  sqlite3.SQLITE_SAVEPOINT):"),
 ('transactions allowed', "    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,\n                  sqlite3.SQLITE_SAVEPOINT):", "    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):"),
 ('any pragma', "    if action == sqlite3.SQLITE_PRAGMA and (arg1 or '').lower() not in PRAGMAS:", "    if False:"),
 ('load_extension allowed', "    if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() == 'load_extension':", "    if False:"),
 ('no step limit', "            return 1 if self.steps > MAX_STEPS else 0", "            return 0"),
 ('no seed', "            upgrade.seed()\n", "\n"),
 ('seed unsandboxed', "        self.db.set_authorizer(_authorizer)  # a migration's triggers fire here too, under the same rules", "        pass"),
 ('no upgrade run', "        if 0 < upto < len(migrations):", "        if False:"),
 ('no lock', "    check_lock(current, delivered)\n", "\n"),
 ('change ok', "        if (mine.name, mine.sha256) != (locked['name'], locked['sha256']):", "        if False:"),
 ('rename ok', "        if (mine.name, mine.sha256) != (locked['name'], locked['sha256']):", "        if mine.sha256 != locked['sha256']:"),
 ('gaps ok', "            if migration.number != expected:", "            if False:"),
 ('no verify', "        fresh.verify(f'{scope} migrations from an empty database')\n", "\n"),
 ('unterminated ok', "        raise SyntaxError('the last statement does not end with ;')", "        pass"),
 ('size unbounded', "                if path.stat().st_size > MAX_FILE:", "                if False:"),
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
