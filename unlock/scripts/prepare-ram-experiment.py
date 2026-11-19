"""Prepare an untested RAM-only experiment for this exact itel firmware."""
import hashlib, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/phone-vault-ramunlock/tools/v9/sources'))
from tb328fu_v9 import parse_image, branch

BASE=0x9efffe00
expected={
 'uboot_a.bin':'2960759e695c3d6dd11c0799fdf272b4a773a34e1a2c3aed2c85bbb02407cd5c',
 'fdl1-dl.bin':'9ff362f4bff3bd3bb91725291d4ded000414b60789dc22496176296df33d5384',
 'sml_a.bin':'624c4e26eb7ed1f2009027c1da14996d6682e6983e25db87d8828d340a56fe1f',
 'teecfg_a.bin':'b292b127817ffadb0c03bd941a676e667007cca421eefc43bbbd2958d1843342',
 'trustos_a.bin':'a33c9e283c2e2ddda36e25d6b613839d2de069c5945cd8a00717ef516417094b'}
data={n:Path(n).read_bytes() for n in expected}
for n,v in data.items():
 if hashlib.sha256(v).hexdigest()!=expected[n]: raise ValueError('Hash mismatch: '+n)
out=Path('ram-experiment');out.mkdir(exist_ok=True)
f=bytearray(data['fdl1-dl.bin'])
assert [struct.unpack_from('<I',f,o)[0] for o in (0x941c,0x9420,0x9424,0x96a4)]==[0xd503201f]*3+[0x52802040]
struct.pack_into('<I',f,0x941c,0xd2b600c0) # mov x0, 0xb0060000 (trustos)
struct.pack_into('<I',f,0x9420,0xd2b60081) # mov x1, 0xb0040000 (teecfg)
struct.pack_into('<I',f,0x96a4,0x52802080) # boot mode 0x104
size=struct.unpack_from('<I',f,0x30)[0];f[8:40]=hashlib.sha256(f[512:512+size]).digest()
(out/'fdl1-boot.bin').write_bytes(f)
u=bytearray(data['uboot_a.bin']); h=parse_image(u);u=u[:h['footprint']]
hook=0x58f4;cave=0x300;setter=0x5ef84
assert struct.unpack_from('<I',u,hook)[0]==0x9400b205 and not any(u[cave:cave+24])
orig=hook+((0x00b205)*4)
for o in range(0x200,0xa0000,4):
 w=struct.unpack_from('<I',u,o)[0]
 if w>>26 in (5,37):
  imm=w&0x3ffffff;imm=imm-(1<<26) if imm&(1<<25) else imm
  if cave<=o+4*imm<cave+24:raise ValueError('Branch into cave')
stub=[0xa9bf7bfd,branch(cave+4,orig,True),0x52800020,branch(cave+12,setter,True),0xa8c17bfd,0xd65f03c0]
struct.pack_into('<I',u,hook,branch(hook,cave,True));struct.pack_into('<6I',u,cave,*stub)
u[8:40]=hashlib.sha256(u[512:512+h['payload_size']]).digest()
(out/'uboot-ram.bin').write_bytes(u)
for n in ('teecfg_a.bin','trustos_a.bin','sml_a.bin'):
 v=data[n];h=parse_image(v);(out/n).write_bytes(v[:h['footprint']])
for p in sorted(out.iterdir()): print(p.name,p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest())
print('EXPERIMENTAL: no hardware execution, no guarantee of valid signature or safe boot')
