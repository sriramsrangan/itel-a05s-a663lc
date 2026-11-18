"""Write a wpa_supplicant.conf with derived PSKs (never raw passwords) from the macOS Keychain (service your-wifi-keychain)."""
import hashlib, subprocess, sys
def pw(account):
    r = subprocess.run(["security", "find-generic-password", "-s", "your-wifi-keychain", "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None
floors = [("Second Floor 5Ghz", ["Second Floor", "Second Floor 5Ghz"], 30),
          ("First Floor MLO",   ["First Floor", "First Floor 5Ghz"], 20),
          ("Ground Floor MLO",  ["Ground Floor", "Ground Floor 5Ghz"], 10)]
out = ["ctrl_interface=/run/wpa_supplicant", "update_config=0", ""]
n = 0
for src, ssids, prio in floors:
    p = pw(src)
    if not p: continue
    for s in ssids:
        psk = hashlib.pbkdf2_hmac("sha1", p.encode(), s.encode(), 4096, 32).hex()
        out += ["network={", f'\tssid="{s}"', f"\tpsk={psk}", "\tkey_mgmt=WPA-PSK", f"\tpriority={prio}", "}", ""]
        n += 1
open(sys.argv[1], "w").write("\n".join(out))
print(f"{n} networks written")
