"""Copy Android binaries' shared-library closure from the handset over adb (read only)."""
import struct, subprocess, sys, os
S = "YOURSERIAL"
SEARCH = ["/vendor/lib64", "/vendor/lib64/hw", "/apex/com.android.vndk.v33/lib64", "/system/lib64",
          "/apex/com.android.runtime/lib64/bionic", "/system/lib64/bootstrap"]
out = sys.argv[1]; bins = sys.argv[2:]
os.makedirs(out + "/lib64", exist_ok=True)

def needed(data):
    shoff = struct.unpack_from('<Q', data, 0x28)[0]; ent, num, _ = struct.unpack_from('<HHH', data, 0x3a)
    secs = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * ent) for i in range(num)]
    dyn = [s for s in secs if s[1] == 6]
    if not dyn: return []
    dyn = dyn[0]; strs = secs[dyn[6]]; res = []
    for i in range(0, dyn[5], 16):
        tag, val = struct.unpack_from('<qQ', data, dyn[4] + i)
        if tag == 1: res.append(data[strs[4] + val:].split(b'\0')[0].decode())
    return res

def pull(path):
    r = subprocess.run(["adb", "-s", S, "exec-out", f"su -c 'cat {path} 2>/dev/null'"], capture_output=True)
    return r.stdout if r.stdout[:4] == b'\x7fELF' else None

seen = {}; queue = []
for b in bins: queue += needed(open(b, 'rb').read())
while queue:
    n = queue.pop()
    if n in seen: continue
    for d in SEARCH:
        data = pull(f"{d}/{n}")
        if data:
            seen[n] = d; open(f"{out}/lib64/{n}", "wb").write(data); queue += needed(data); break
    else:
        seen[n] = None
for n, d in sorted(seen.items()): print(f"{n:40} {d}")
