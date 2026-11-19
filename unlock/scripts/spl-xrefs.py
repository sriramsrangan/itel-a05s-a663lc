import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root / 'vendor'))
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM,ARM64_OP_REG
d=(root / 'working/splloader.bin').read_bytes();base=0x5000
names=[b'sml_a',b'teecfg_a',b'trustos_a',b'uboot_a',b'tee_sml_addr',b'tee_sml_size']
targets={base+d.index(n):n.decode() for n in names if n in d}
print([(hex(k),v) for k,v in targets.items()])
m=Cs(CS_ARCH_ARM64,CS_MODE_ARM);m.detail=True;m.skipdata=True;seen={}
for i in m.disasm(d[0x200:0xda00],base+0x200):
 if not i.id: continue
 o=i.operands
 if i.mnemonic=='adrp' and len(o)==2 and o[1].type==ARM64_OP_IMM: seen[i.reg_name(o[0].reg)]=(o[1].imm,i.address)
 if i.mnemonic=='add' and len(o)==3 and o[1].type==ARM64_OP_REG and o[2].type==ARM64_OP_IMM:
  r=i.reg_name(o[1].reg)
  if r in seen and i.address-seen[r][1]<64:
   v=seen[r][0]+o[2].imm
   if v in targets: print(hex(i.address),targets[v])
