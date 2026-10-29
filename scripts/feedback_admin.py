"""Local owner utilities: copy the private management key, or make a consistent backup."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.feedback import FeedbackStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,help='Defaults to PHONE_FEEDBACK_DB or local_state/feedback.sqlite3')
    parser.add_argument('--key-file',type=Path,help='Use an already downloaded private management key (copy only)')
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--copy-key',action='store_true',help='Copy the management key to Windows clipboard; never print it')
    actions.add_argument('--backup',type=Path,metavar='OUTPUT',help='Create a new SQLite backup file; never overwrite an earlier backup')
    args=parser.parse_args();store=FeedbackStore(args.db)
    if args.key_file and not args.copy_key:parser.error('--key-file is only for --copy-key')
    if args.copy_key:
        if os.name!='nt':parser.error('--copy-key requires Windows; read the server key file in a private terminal instead')
        key=args.key_file.read_text(encoding='ascii').strip() if args.key_file else store._key()
        if len(key)<32:parser.error('Management key is incomplete')
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
                        '$taskFeedbackKey=[Console]::In.ReadToEnd(); Set-Clipboard -Value $taskFeedbackKey'],
                       input=key,text=True,capture_output=True,check=True)
        print('管理密钥已复制。请粘贴到反馈管理页；操作结束后清空剪贴板。')
    else:
        store.backup(args.backup)
        print('Feedback backup created: '+str(args.backup.resolve()))


if __name__=='__main__':main()
