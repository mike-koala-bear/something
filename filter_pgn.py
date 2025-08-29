# pylint: disable=unused-variable, unused-argument
#!/usr/bin/env python3
# filter_pgn.py  - streaming PGN sanitizer
import sys, re
buff=[]
for line in sys.stdin:
    buff.append(line)
    if line.strip()=="":
        out=[]
        for L in buff:
            L = re.sub(r"\{[^}]*\}","",L)   # remove {...}
            L = re.sub(r"\$\d+","",L)      # remove NAGs like $1
            # remove simple parentheses (variations) iteratively
            while "(" in L:
                L = re.sub(r"\([^()]*\)","",L)
            out.append(L)
        sys.stdout.write(''.join(out) + "\n")
        buff=[]
