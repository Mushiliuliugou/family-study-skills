#!/usr/bin/env python3
"""Check distributed Skill bytes and command entrypoints without a study database."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def verify(root=ROOT,workbench_skills=None,check_commands=True):
    root=Path(root);release=json.loads((root/'release.json').read_text())
    if (root/'VERSION').read_text().strip()!=release['version']:raise ValueError('Version mismatch')
    listed={r['path']:r['sha256'] for r in release['files']}
    actual={p.relative_to(root).as_posix():p for p in (root/'skills').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    if set(actual)!=set(listed):raise ValueError('Missing or unexpected Skill files')
    for name,path in actual.items():
        if path.is_symlink() or path.suffix not in ('.py','.md','.yaml'):raise ValueError('Unexpected file type: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=listed[name]:raise ValueError('File checksum mismatch: '+name)
        if path.suffix=='.py':ast.parse(path.read_text())
        if path.suffix=='.md':
            for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
                if '://' not in link and not link.startswith('#') and not (path.parent/link.split('#')[0]).exists():raise ValueError('Missing local reference: '+link)
    for name,expected in release['skill_fingerprints'].items():
        folder=root/'skills'/name;digest=hashlib.sha256()
        for path in sorted(folder.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.md','.yaml'):
                digest.update(path.relative_to(folder).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
        if digest.hexdigest()!=expected:raise ValueError('Skill fingerprint mismatch: '+name)
        if workbench_skills:
            for path in (root/'skills'/name).rglob('*'):
                if path.is_file():
                    local=Path(workbench_skills)/name/path.relative_to(folder)
                    if not local.is_file() or local.read_bytes()!=path.read_bytes():raise ValueError('Workbench copy differs: '+str(local))
    commands=[]
    if check_commands:
        for path in sorted((root/'skills').glob('*/scripts/*.py')):
            run=subprocess.run([sys.executable,'-B',str(path),'--help'],cwd=root,capture_output=True,text=True,timeout=20)
            if run.returncode:raise ValueError('Command failed: '+path.name)
            commands.append(path.relative_to(root).as_posix())
    return {'version':release['version'],'skill_files':len(actual),'command_entrypoints':commands,'database_accessed':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workbench-skills',type=Path);args=parser.parse_args()
    try:print(json.dumps(verify(workbench_skills=args.workbench_skills),ensure_ascii=False,indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')
