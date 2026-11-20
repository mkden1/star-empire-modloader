import marshal, struct, sys, types, zlib
EXE, MOD = sys.argv[1:3]
data = open(EXE,'rb').read(); cp = data.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
_, plen, tpos, tlen, _, _ = struct.unpack('!8sIIII64s', data[cp:cp+88]); ps = cp+88-plen
toc, i, pyz = data[ps+tpos:ps+tpos+tlen], 0, None
while i < len(toc):
    el, ep, dl, ul, cf, tc = struct.unpack('!IIIIBc', toc[i:i+18])
    if tc in (b'z', b'Z'):
        b = data[ps+ep:ps+ep+dl]; pyz = zlib.decompress(b) if cf else b
    i += el
off, = struct.unpack('!i', pyz[8:12]); table = dict(marshal.loads(pyz[off:]))
_, pos, ln = table[MOD]; root = marshal.loads(zlib.decompress(pyz[pos:pos+ln]))
def walk(co):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType): yield from walk(c)
for co in walk(root):
    if '<' in co.co_qualname and co.co_qualname != '<module>': continue
    # class bodies store their doc via STORE_NAME __doc__; functions: HAS_DOCSTRING flag 0x4000000
    if co.co_flags & 0x4000000 and co.co_consts and isinstance(co.co_consts[0], str):
        print(f"### {co.co_qualname}\n{co.co_consts[0]!r}\n")
    elif co.co_name and co.co_firstlineno and co.co_consts:
        for c in co.co_consts:
            if isinstance(c, str) and len(c) > 60 and '\n' in c and co.co_flags == 0x0:
                print(f"### (class body) {co.co_qualname}\n{c!r}\n")
