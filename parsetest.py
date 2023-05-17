from __future__ import annotations

from pathlib import Path
import sys
EDB_DIR = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(EDB_DIR))

from typing import *

from edb import edgeql
from edb.edgeql import codegen as qlcodegen
from edb.edgeql import parser
from edb.edgeql import ast as qlast

def parse(querystr: str) -> qlast.Expr:
    source = edgeql.Source.from_string(querystr)
    query = parser.parse_fragment_with_recovery(source)
    return query

QS = [
'''
select User { name,
''',
'''
select User.
''',
'''
update User set {
''',
'''
update User set { foo := 1,
''',
'''
select User { foo } filter .
''',
'''
with x := (select User filter .
''',
'''
with new_users := (
  for name in {'foo', 'bar'} union (
    insert User { name :=
''',
'''
for name in User.
''',
]


def main() -> None:
    for qry in QS:
        print(qry.rstrip())
        print(' =>')
        q = parse(qry)
        code = qlcodegen.generate_source(q)
        print(code)
        print('===')


if __name__ == '__main__':
    main()
