import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'vendor'))
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG
data=(root / 'working/uboot_a.bin').read_bytes()
base=0x9efffe00
needles=[b'Ve' + b'rifiedBoot-UNLOCK',b'set_lock_status failed',b'set device status unlock',b'enter boot mode g_mode_str',b'get boot mode in boot func array',b'ANDROID: Booting slot']
targets={base+data.index(n):n.decode() for n in needles if n in data}
print('targets', [(hex(k),v) for k,v in targets.items()])
md=Cs(CS_ARCH_ARM64,CS_MODE_ARM); md.detail=True; md.skipdata=True
last={}
for ins in md.disasm(data[0x200:0x160000],base+0x200):
    if not ins.id: continue
    ops=ins.operands
    if ins.mnemonic=='adrp' and len(ops)==2 and ops[1].type==ARM64_OP_IMM:
        last[ins.reg_name(ops[0].reg)]=(ops[1].imm,ins.address)
    elif ins.mnemonic=='add' and len(ops)==3 and ops[1].type==ARM64_OP_REG and ops[2].type==ARM64_OP_IMM:
        reg=ins.reg_name(ops[1].reg)
        if reg in last and ins.address-last[reg][1]<64:
            val=last[reg][0]+ops[2].imm
            if val in targets: print(hex(ins.address), targets[val],ins.mnemonic,ins.op_str)
