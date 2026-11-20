"""Read-only: print disassembly of named functions from the frozen exe, grouped by
source line. usage: show_func.py <exe> <module> <qualname> [...]   ('*' = all, minus comprehensions)"""
import dis, marshal, struct, sys, types, zlib
EXE, MOD, *NAMES = sys.argv[1:]
data = open(EXE, 'rb').read(); cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp + 88]); ps = cp + 88 - plen
toc, i, pyz = data[ps + tpos:ps + tpos + tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i + 18])
    if tc in (b'z', b'Z'):
        b = data[ps + ep:ps + ep + dl]; pyz = zlib.decompress(b) if cf else b
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = dict(marshal.loads(pyz[off:]))
_, pos, ln = table[MOD]; root = marshal.loads(zlib.decompress(pyz[pos:pos + ln]))


def walk(co):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from walk(c)


SKIP = ('CACHE', 'RESUME', 'NOP', 'NOT_TAKEN')
for co in walk(root):
    if '*' in NAMES and ('<' in co.co_qualname and co.co_qualname != '<module>'):
        continue
    if '*' in NAMES or co.co_qualname in NAMES:
        doc = co.co_consts[0] if co.co_consts and isinstance(co.co_consts[0], str) else None
        nargs = co.co_argcount + co.co_kwonlyargcount
        print(f"\n##### {co.co_qualname} line {co.co_firstlineno} args={co.co_varnames[:nargs]} flags={co.co_flags:#x} doc={doc!r}")
        last = None
        for ins in dis.get_instructions(co):
            if ins.opname in SKIP:
                continue
            ln_ = ins.line_number
            if ln_ != last:
                print(f"  -- L{ln_}"); last = ln_
            a = ins.argrepr
            if isinstance(ins.argval, types.CodeType):
                a = f"<code {ins.argval.co_qualname}>"
            elif len(a) > 130:
                a = a[:130] + '...'
            tgt = ' *' if ins.is_jump_target else ''
            print(f"    {ins.opname:<22} {a}{tgt}")
