#!/usr/bin/env bash
set -Eeuo pipefail

MUX_REPOSITORY="https://github.com/Hoser01/meshtastic-tcp-mux.git"
MUX_REF="${ATLAS_MUX_REF:-main}"
APP_DIR="/opt/meshtastic-tcp-mux"

die() { echo "ERROR: $*" >&2; exit 1; }
prompt() { local label=$1 default=${2:-} value; read -r -p "$label${default:+ [$default]}: " value; printf '%s' "${value:-$default}"; }
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( 1 <= $1 && $1 <= 65535 )); }

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo is required"
  exec sudo -- "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv git ca-certificates curl

discovery=$(python3 <<'PY'
import ast, glob, pathlib
candidates=[]
for pattern in (
    "/opt/lzmesh-feeder/*.py", "/opt/lzfeeder/*.py",
    "/home/*/lzmesh-feeder/*.py", "/home/*/chaos/lzmesh-feeder/*.py",
    "/usr/local/lib/lzmesh-feeder/*.py",
):
    candidates.extend(glob.glob(pattern))
for name in candidates:
    path=pathlib.Path(name)
    try: tree=ast.parse(path.read_text(errors="replace"))
    except Exception: continue
    values={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            key=node.targets[0].id
            if key in {"NODE_HOST","NODE_PORT","FEEDER_ID"}:
                try: values[key]=ast.literal_eval(node.value)
                except Exception: pass
    if "NODE_HOST" in values:
        print(f"{path}\t{values.get('NODE_HOST','')}\t{values.get('NODE_PORT',4403)}\t{values.get('FEEDER_ID','')}")
        break
PY
)

legacy_file=""; discovered_host=""; discovered_port=""
if [[ -n "$discovery" ]]; then
  IFS=$'\t' read -r legacy_file discovered_host discovered_port _ <<<"$discovery"
  echo "Discovered legacy feeder configuration: $legacy_file"
  echo "  node endpoint: $discovered_host:$discovered_port"
fi

if [[ -f "$APP_DIR/meshtastic_tcp_mux.py" ]]; then
  existing=$(python3 - "$APP_DIR/meshtastic_tcp_mux.py" <<'PY'
import ast,sys
tree=ast.parse(open(sys.argv[1]).read()); values={}
for node in tree.body:
    if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
        if node.targets[0].id in {"REAL_NODE_HOST","REAL_NODE_PORT","LISTEN_PORT"}:
            try: values[node.targets[0].id]=ast.literal_eval(node.value)
            except Exception: pass
print(f"{values.get('REAL_NODE_HOST','')}\t{values.get('REAL_NODE_PORT','')}\t{values.get('LISTEN_PORT','')}")
PY
  )
  IFS=$'\t' read -r existing_host existing_port existing_listen <<<"$existing"
  discovered_host=${existing_host:-$discovered_host}
  discovered_port=${existing_port:-$discovered_port}
  default_listen=${existing_listen:-4405}
  mode=upgrade
else
  default_listen=4405
  mode=new
fi

upstream_host=$(prompt "Physical Meshtastic node host/IP" "${discovered_host:-127.0.0.1}")
upstream_port=$(prompt "Physical Meshtastic TCP port" "${discovered_port:-4403}")
listen_port=$(prompt "MUX client listen port" "$default_listen")
valid_port "$upstream_port" || die "invalid upstream port"
valid_port "$listen_port" || die "invalid listen port"
[[ "$upstream_host:$upstream_port" != "127.0.0.1:$listen_port" && "$upstream_host:$upstream_port" != "localhost:$listen_port" ]] || die "upstream and MUX endpoints would loop"

python3 - "$upstream_host" "$upstream_port" <<'PY' || die "cannot reach the physical Meshtastic TCP endpoint"
import socket,sys
with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5): pass
PY

if ss -ltnH "sport = :$listen_port" | grep -q .; then
  if ! systemctl is-active --quiet meshtastic-tcp-mux.service; then
    die "TCP port $listen_port is already in use by another process"
  fi
fi
if ss -ltnH "sport = :4406" | grep -q . && ! systemctl is-active --quiet meshtastic-tcp-mux.service; then
  die "TCP port 4406 is already in use; the localhost MUX audit listener cannot be enabled safely"
fi

work_dir=$(mktemp -d /tmp/atlas-mux-migration.XXXXXX)
trap 'case "$work_dir" in /tmp/atlas-mux-migration.*) rm -rf -- "$work_dir";; esac' EXIT
git clone --depth 1 --branch "$MUX_REF" "$MUX_REPOSITORY" "$work_dir/mux"

# Install without starting against repository defaults; site values are patched first.
printf 'n\n' | (cd "$work_dir/mux" && ./install.sh --mode "$mode" --enable-audit)
python3 - "$APP_DIR/meshtastic_tcp_mux.py" "$upstream_host" "$upstream_port" "$listen_port" <<'PY'
import ast,re,sys
from pathlib import Path
path=Path(sys.argv[1]); values={"REAL_NODE_HOST":repr(sys.argv[2]),"REAL_NODE_PORT":repr(int(sys.argv[3])),"LISTEN_HOST":repr("0.0.0.0"),"LISTEN_PORT":repr(int(sys.argv[4]))}
lines=path.read_text().splitlines(); changed=set()
for index,line in enumerate(lines):
    match=re.match(r"^([A-Z][A-Z0-9_]*)\s*=",line)
    if match and match.group(1) in values:
        key=match.group(1); lines[index]=f"{key} = {values[key]}"; changed.add(key)
missing=set(values)-changed
if missing: raise SystemExit("missing MUX settings: "+", ".join(sorted(missing)))
path.write_text("\n".join(lines)+"\n")
PY

"$APP_DIR/venv/bin/python" "$APP_DIR/meshtastic_tcp_mux.py" --version
"$APP_DIR/venv/bin/python" "$APP_DIR/meshtastic_tcp_mux.py" --check
systemctl restart meshtastic-tcp-mux.service
sleep 3
systemctl is-active --quiet meshtastic-tcp-mux.service || { journalctl -u meshtastic-tcp-mux.service -n 80 --no-pager; die "MUX service failed"; }
python3 - "127.0.0.1" "$listen_port" <<'PY' || die "MUX listener self-test failed"
import socket,sys
with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5): pass
PY
python3 - "127.0.0.1" 4406 <<'PY' || die "MUX audit listener self-test failed"
import socket,sys
with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5): pass
PY

echo
echo "MUX installation and self-tests passed."
echo "  upstream: $upstream_host:$upstream_port"
echo "  clients:  127.0.0.1:$listen_port"
echo "  audit:    127.0.0.1:4406"
if [[ -n "$legacy_file" ]]; then
  echo "Legacy feeder remains enabled until the collector passes its end-to-end self-test."
fi
