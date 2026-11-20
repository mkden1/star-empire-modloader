"""Read-only verifier: compile a candidate .py and compare each function with the
frozen function of the same qualname in Client.exe, as normalized instruction streams
(opname + argument; line numbers and the __firstlineno__ constant are ignored; docstrings kept).

usage: verify_port.py <exe> <module> <candidate.py> [--diff Qualname] [Qualname ...]
"""
import dis, marshal, struct, sys, types, zlib

args = sys.argv[1:]
EXE, MOD, CAND = args[:3]
rest = args[3:]
DIFF = None
if '--diff' in rest:
    k = rest.index('--diff'); DIFF = rest[k + 1]; rest = rest[:k] + rest[k + 2:]
ONLY = rest
import os
LOOSE = bool(os.environ.get('LOOSE'))

data = open(EXE, 'rb').read(); cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp + 88]); ps = cp + 88 - plen
toc, i, pyz = data[ps + tpos:ps + tpos + tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i + 18])
    if tc in (b'z', b'Z'):
        b = data[ps + ep:ps + ep + dl]; pyz = zlib.decompress(b) if cf else b
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = dict(marshal.loads(pyz[off:]))
_, pos, ln = table[MOD]
official = marshal.loads(zlib.decompress(pyz[pos:pos + ln]))
mine = compile(open(CAND, 'rb').read(), CAND, 'exec')

SKIP = ('CACHE', 'RESUME', 'NOP', 'NOT_TAKEN')


def stream(co):
    out = []
    doc = co.co_consts[0] if co.co_consts and isinstance(co.co_consts[0], str) else None
    out.append(('DOC', doc))
    ins = [x for x in dis.get_instructions(co) if x.opname not in SKIP]
    for k, x in enumerate(ins):
        arg = x.argrepr
        if isinstance(x.argval, types.CodeType):
            arg = f"<code {x.argval.co_qualname}>"
        elif x.opname in ('LOAD_SMALL_INT', 'LOAD_CONST') and k + 1 < len(ins) \
                and ins[k + 1].opname == 'STORE_NAME' and ins[k + 1].argval == '__firstlineno__':
            arg = '<lineno>'
        elif x.opname == 'LOAD_CONST' and isinstance(x.argval, str) and len(x.argval) > 400:
            arg = repr(x.argval)
        if LOOSE and isinstance(arg, str) and arg.startswith('to L'):
            arg = 'to *'
        out.append((x.opname, arg))
    return out


def funcs(co, out):
    out[co.co_qualname] = co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            funcs(c, out)
    return out


of, mf = funcs(official, {}), funcs(mine, {})
if DIFF:
    a, b = stream(of[DIFF]), stream(mf[DIFF])
    print(f"official={len(a)} instr, mine={len(b)} instr")
    for k in range(max(len(a), len(b))):
        x = a[k] if k < len(a) else None; y = b[k] if k < len(b) else None
        if x != y:
            print(f"first difference at instruction {k}")
            for j in range(max(0, k - 6), min(max(len(a), len(b)), k + 10)):
                xa = a[j] if j < len(a) else None; ya = b[j] if j < len(b) else None
                mark = '>>' if xa != ya else '  '
                print(f"  {mark} {j:4d} OFFICIAL {str(xa)[:78]:<78} | MINE {str(ya)[:78]}")
            if x and y and x[0] == y[0] and (len(str(x[1])) > 70 or len(str(y[1])) > 70):
                import ast
                try:
                    ox, my = ast.literal_eval(x[1]), ast.literal_eval(y[1])
                    if isinstance(ox, tuple) and isinstance(my, tuple):
                        print("  only in OFFICIAL:", [e for e in ox if e not in my])
                        print("  only in MINE    :", [e for e in my if e not in ox])
                        print("  same set, different order:", set(ox) == set(my) and ox != my)
                    else:
                        print("  OFFICIAL full:", x[1]); print("  MINE full    :", y[1])
                except Exception:
                    print("  OFFICIAL full:", x[1]); print("  MINE full    :", y[1])
            break
    else:
        print("IDENTICAL")
    sys.exit(0)

ok = bad = 0; missing = []; extra = sorted(set(mf) - set(of)); mism = []
for q in sorted(of):
    if ONLY and q not in ONLY:
        continue
    if q not in mf:
        missing.append(q); continue
    if stream(of[q]) == stream(mf[q]):
        ok += 1
    else:
        bad += 1; mism.append(q)
print(f"{MOD}: identical={ok} mismatched={bad} missing={len(missing)} extra={len(extra)}")
if mism:
    print("MISMATCH:", ", ".join(m for m in mism if '<' not in m or m == '<module>'))
if missing:
    print("MISSING :", ", ".join(m for m in missing if '<' not in m))
if extra:
    print("EXTRA   :", ", ".join(e for e in extra if '<' not in e))
