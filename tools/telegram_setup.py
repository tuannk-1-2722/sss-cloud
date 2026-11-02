"""Prepare Telegram secrets on the existing SSH host. Never enables or sends notifications."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import shlex
import subprocess
from pathlib import Path

REMOTE = r"""
import json,sys,os,pathlib,re,logging
logging.getLogger('httpx').setLevel(logging.WARNING)
try:
 import httpx
 payload=json.load(sys.stdin);token=payload['token']
 if len(token)>512 or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):
  print(json.dumps({'ok':False,'reason':'BOT_TOKEN_INVALID'}));sys.exit(0)
 def api(method,params=None):
  r=httpx.get('https://api.telegram.org/bot'+token+'/'+method,params=params,timeout=8,trust_env=False,follow_redirects=False)
  data=r.json()
  if not isinstance(data,dict) or data.get('ok') is not True:raise ValueError('PROVIDER_REJECTED')
  return data['result']
 bot=api('getMe');hook=api('getWebhookInfo')
 if hook.get('url'):
  print(json.dumps({'ok':False,'reason':'BOT_HAS_WEBHOOK_USE_DEDICATED_BOT'}));sys.exit(0)
 username=bot['username']
 if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):raise ValueError('BOT_USERNAME_INVALID')
 if payload['phase']=='inspect':
  print(json.dumps({'ok':True,'username':username}));sys.exit(0)
 updates=api('getUpdates',{'limit':100,'timeout':0,'allowed_updates':json.dumps(['message'])})
 recipients=set()
 for update in updates:
  message=update.get('message',{});chat=message.get('chat',{})
  if message.get('text')=='/start '+payload['nonce'] and chat.get('type')=='private' and type(chat.get('id')) is int and chat['id']>0:
   recipients.add(chat['id'])
 if len(recipients)!=1:
  print(json.dumps({'ok':False,'reason':'PRIVATE_START_NOT_FOUND'}));sys.exit(0)
 directory=pathlib.Path(payload['private_dir']).resolve()
 if not directory.is_dir() or directory.stat().st_uid!=os.getuid() or directory.stat().st_mode & 0o007:
  print(json.dumps({'ok':False,'reason':'PRIVATE_DIRECTORY_PERMISSIONS'}));sys.exit(0)
 target=directory/'telegram.env'
 if target.is_symlink() or (target.exists() and not payload['replace']):
  print(json.dumps({'ok':False,'reason':'CREDENTIAL_FILE_EXISTS'}));sys.exit(0)
 text='SSS_TELEGRAM_TOKEN='+token+chr(10)+'SSS_TELEGRAM_CHAT='+str(next(iter(recipients)))+chr(10)+'SSS_TELEGRAM_PHOTO_CONSENT='+('1' if payload['photos'] else '0')+chr(10)
 import secrets
 temp=directory/('.telegram-'+secrets.token_hex(8)+'.tmp')
 try:
  fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
  if not payload['replace']:
   os.link(temp,target) # Atomic no-clobber even if another setup runs concurrently.
  else:os.replace(temp,target)
 finally:temp.unlink(missing_ok=True)
 print(json.dumps({'ok':True,'username':username,'credentials_file':str(target),'photos_consented':payload['photos'],'sent_messages':0}))
except Exception:
 print(json.dumps({'ok':False,'reason':'TELEGRAM_SETUP_FAILED_CHECK_NETWORK_TOKEN_OR_FILE'}))
"""


def remote_setup(args, payload):
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(args.identity),
        args.ssh_host,
        shlex.quote(args.remote_python) + " -B -c " + shlex.quote(REMOTE),
    ]
    # Token is stdin data, never argv, a generated script, shell history, or log output.
    try:
        result = subprocess.run(
            command, input=json.dumps(payload), text=True, capture_output=True, timeout=45
        )
        if result.returncode:
            raise RuntimeError("SSH_SETUP_FAILED")
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("SSH_SETUP_FAILED") from exc
    if not data.get("ok"):
        raise RuntimeError(data.get("reason", "TELEGRAM_SETUP_FAILED"))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--private-dir", required=True)
    parser.add_argument("--consent-photos", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace a previous Telegram recipient/token",
    )
    args = parser.parse_args()
    if not args.identity.is_file():
        parser.error("SSH identity file not found")
    token = getpass.getpass("Token bot từ BotFather (ẩn, không gửi vào chat): ").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or len(token) > 512:
        parser.error("Bot token has invalid format")
    common = {
        "token": token,
        "private_dir": args.private_dir,
        "photos": args.consent_photos,
        "replace": args.replace,
    }
    bot = remote_setup(args, {**common, "phase": "inspect"})
    nonce = secrets.token_urlsafe(24)
    print(f"Trên ĐIỆN THOẠI muốn nhận cảnh báo, mở https://t.me/{bot['username']}?start={nonce}")
    print("Bấm START/BẮT ĐẦU. Bot chưa cần trả lời; công cụ dùng mã riêng để ghép đúng người nhận.")
    input("Sau khi đã bấm Start trên đúng điện thoại, nhấn Enter tại đây: ")
    result = remote_setup(args, {**common, "phase": "bind", "nonce": nonce})
    print(
        f"Đã ghép bot @{result['username']} với người nhận và lưu bí mật trên server, quyền file600."
    )
    print(
        "Chưa bật gửi tự động và chưa gửi thông báo thử. Báo lại trong chat: Đã ghép bot Telegram."
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Chưa thiết lập được: {exc}")
        raise SystemExit(1) from None
