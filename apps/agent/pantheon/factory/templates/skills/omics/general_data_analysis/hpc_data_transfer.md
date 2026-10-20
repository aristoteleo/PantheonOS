---
id: hpc_data_transfer
name: HPC Data Transfer via SSH
description: |
  Transfer data between local machine and HPC clusters via SSH.
  Covers SSH ControlMaster for persistent connections, rsync for
  efficient/resumable transfers, and pexpect for MFA authentication.
tags: [ssh, hpc, rsync, data-transfer, scp]
---

# HPC Data Transfer via SSH

Efficient, resumable data transfer to/from HPC clusters using SSH
ControlMaster (single authentication, persistent connection) and rsync.

## Workflow

### 1. Establish a Persistent SSH Connection

SSH ControlMaster lets you authenticate once and reuse the connection for
all subsequent SSH/rsync/scp commands — no repeated password prompts.

```bash
# Open a persistent connection (authenticates once)
ssh -o ControlMaster=yes \
    -o ControlPath=/tmp/hpc-ssh \
    -o ControlPersist=3600 \
    <host> "echo connected"

# All subsequent commands reuse this connection automatically:
ssh -o ControlPath=/tmp/hpc-ssh <host> "ls /data/"
```

| Parameter | Purpose |
|---|---|
| `ControlMaster=yes` | This connection becomes the shared master |
| `ControlPath=/tmp/hpc-ssh` | Socket file for connection sharing |
| `ControlPersist=3600` | Keep alive for 1 hour after last use |

### 2. Transfer Data with rsync

rsync is preferred over scp: it compresses, skips existing files, and
resumes interrupted transfers.

```bash
# Download: remote → local
rsync -avz --progress \
  -e "ssh -o ControlPath=/tmp/hpc-ssh" \
  <host>:/remote/path/ \
  /local/path/

# Upload: local → remote
rsync -avz --progress \
  -e "ssh -o ControlPath=/tmp/hpc-ssh" \
  /local/path/ \
  <host>:/remote/path/
```

For interrupted transfers, re-run the same command with `--partial` —
rsync will skip completed files and resume partial ones.

### 3. Parallel Downloads

For multiple independent directories, run rsync processes in parallel:

```python
import subprocess

CONTROL_PATH = "/tmp/hpc-ssh"
HOST = "<host>"

transfers = [
    ("/remote/dir1/", "/local/dir1/"),
    ("/remote/dir2/", "/local/dir2/"),
    ("/remote/dir3/", "/local/dir3/"),
]

procs = []
for remote, local in transfers:
    p = subprocess.Popen([
        "rsync", "-avz", "--progress",
        "-e", f"ssh -o ControlPath={CONTROL_PATH}",
        f"{HOST}:{remote}", local
    ])
    procs.append((remote, p))

for remote, p in procs:
    p.wait()
    print(f"{'OK' if p.returncode == 0 else 'FAILED'}: {remote}")
```

### 4. Verify Transfer Completeness

```python
import subprocess
from pathlib import Path

def ssh_cmd(cmd, control_path=CONTROL_PATH, host=HOST):
    result = subprocess.run(
        ["ssh", "-o", f"ControlPath={control_path}", host, cmd],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()

remote_count = int(ssh_cmd(f"find /remote/path/ -type f | wc -l"))
local_count = sum(1 for _ in Path("/local/path/").rglob("*") if _.is_file())
print(f"Remote: {remote_count}, Local: {local_count}")
assert remote_count == local_count, "File count mismatch — re-run rsync"
```

### 5. Clean Up

```bash
# Close the persistent connection
ssh -o ControlPath=/tmp/hpc-ssh -O exit <host>
```

The connection also closes automatically after `ControlPersist` seconds of
inactivity.

## Handling MFA / Interactive Authentication (e.g. Duo)

Some HPC systems (e.g. Stanford Sherlock) require interactive
authentication (password + Duo push / TOTP). Since `BatchMode=yes` cannot
handle this, use `pexpect` to automate the interactive prompts.

> [!IMPORTANT]
> Ask the user for their password before calling this function. Do not store
> or log it. For Duo push, the user must approve on their phone.

```python
import pexpect
import sys

def establish_ssh_with_mfa(host, password, control_path="/tmp/hpc-ssh"):
    """
    Establish SSH ControlMaster through interactive MFA authentication.
    Handles password prompt and Duo push (sends '1' to select push).
    """
    child = pexpect.spawn(
        f'ssh -o ControlMaster=yes -o ControlPath={control_path} '
        f'-o ControlPersist=3600 {host} "echo CONNECTED_OK"',
        timeout=60
    )
    child.logfile_sys = sys.stdout.buffer

    idx = child.expect(
        ['[Pp]assword:', 'CONNECTED_OK', pexpect.EOF, pexpect.TIMEOUT],
        timeout=30
    )
    if idx == 0:
        child.sendline(password)
        idx2 = child.expect(
            ['Duo', 'CONNECTED_OK', '[Pp]assword:', pexpect.EOF, pexpect.TIMEOUT],
            timeout=30
        )
        if idx2 == 0:
            child.sendline('1')  # select Duo Push
            idx3 = child.expect(
                ['CONNECTED_OK', 'Success', pexpect.EOF, pexpect.TIMEOUT],
                timeout=120
            )
            if idx3 not in [0, 1]:
                raise RuntimeError(f"MFA approval failed or timed out")
        elif idx2 == 1:
            pass  # connected without MFA
        elif idx2 == 2:
            raise RuntimeError("Password rejected")
    elif idx == 1:
        pass  # already connected

    child.expect(pexpect.EOF, timeout=30)
    child.close()
    if child.exitstatus != 0:
        raise RuntimeError(f"SSH failed with exit status {child.exitstatus}")
    return True
```

**Adapting for other MFA methods:**
- **TOTP (Google Authenticator, etc.)**: Replace `child.sendline('1')` with
  `child.sendline('<totp_code>')` and adjust the expect pattern from `'Duo'`
  to the prompt your system shows (e.g. `'Verification code:'`).
- **No MFA**: If password-only, the `idx2 == 0` (Duo) branch is simply skipped.
- **SSH key auth**: No pexpect needed — standard ControlMaster works directly.

## Troubleshooting

- **"Permission denied"**: ControlMaster session expired. Re-establish with Step 1.
- **rsync missing files**: Re-run the same rsync command — it only transfers
  missing or changed files.
- **MFA timeout**: Increase pexpect timeout (default 120s). For Duo, user must
  approve within this window.
- **Multiple HPC hosts**: Use different `ControlPath` values per host
  (e.g. `/tmp/sherlock-ssh`, `/tmp/slac-ssh`).
- **Connection dropped mid-transfer**: Add `--partial` to rsync to keep
  partially transferred files for resumption.
