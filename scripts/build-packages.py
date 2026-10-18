#!/usr/bin/env python3
"""Build architecture-independent LuCI IPKs and stage native APKv3 inputs.

IPKs follow OpenWrt scripts/ipkg-build's gzip/tar container (not Debian ar).
APK files must be produced by apk-tools v3 mkpkg using build-apk.sh.
Only Python's standard library is needed. No source files execute during build.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '0.1.1'
EPOCH = int(os.environ.get('SOURCE_DATE_EPOCH', '1792368000'))
PACKAGES = {
 'luci-theme-openwrtstudio': ('OpenWrt Studio dark theme', ['luci-theme-bootstrap'], 'MIT AND Apache-2.0'),
 'luci-app-openwrtstudio': ('OpenWrt Studio control center', ['luci-base','luci-mod-status','luci-mod-network','luci-mod-system','luci-app-firewall'], 'MIT'),
}

POST_THEME = '''#!/bin/sh
set -eu
[ -n "${IPKG_INSTROOT:-}" ] && exit 0
sh /etc/uci-defaults/90-openwrtstudio-theme
rm -f /tmp/luci-indexcache.*
exit 0
'''
POST_APP = '''#!/bin/sh
set -eu
[ -n "${IPKG_INSTROOT:-}" ] && exit 0
rm -f /tmp/luci-indexcache.*
if [ -x /etc/init.d/rpcd ]; then /etc/init.d/rpcd reload; fi
exit 0
'''
PRE_REMOVE_THEME = '''#!/bin/sh
set -eu
[ -n "${IPKG_INSTROOT:-}" ] && exit 0
[ "${1:-}" = upgrade ] && exit 0
if [ "$(uci -q get luci.main.mediaurlbase || true)" = /luci-static/openwrtstudio ]; then
 original=$(cat /etc/openwrtstudio-original-theme 2>/dev/null || true)
 case "$original" in /luci-static/*) ;; *) original=/luci-static/bootstrap;; esac
 theme=${original##*/}
 if [ ! -f "/usr/share/ucode/luci/template/themes/$theme/header.ut" ] || [ "$theme" = openwrtstudio ]; then
  original=/luci-static/bootstrap
 fi
 uci set "luci.main.mediaurlbase=$original"
fi
uci -q delete luci.themes.OpenWrtStudio || true
uci commit luci
rm -f /tmp/luci-indexcache.*
exit 0
'''

def tar_gz(entries):
 raw=io.BytesIO()
 with tarfile.open(fileobj=raw,mode='w',format=tarfile.GNU_FORMAT) as archive:
  directories=set()
  for name in entries:
   directories.update(p.as_posix() for p in Path(name).parents if p.as_posix()!='.')
  for name in sorted(directories,key=lambda n:(n.count('/'),n)):
   info=tarfile.TarInfo('./'+name+'/');info.type=tarfile.DIRTYPE
   info.mode=0o755;info.mtime=EPOCH;info.uid=info.gid=0
   archive.addfile(info)
  for name,(content,mode) in sorted(entries.items()):
   info=tarfile.TarInfo('./'+name)
   info.size=len(content);info.mode=mode;info.mtime=EPOCH
   info.uid=info.gid=0;info.uname=info.gname=''
   archive.addfile(info,io.BytesIO(content))
 return gzip.compress(raw.getvalue(),mtime=0)

def payload(name):
 entries={}
 for base,prefix in [('htdocs','www/'),('root','')]:
  for file in sorted((ROOT/name/base).rglob('*')):
   if file.is_file():
    path=prefix+file.relative_to(ROOT/name/base).as_posix()
    data=file.read_bytes()
    if file.suffix in ('.js','.css','.json','.ut') or '/uci-defaults/' in '/'+path:
     data=data.replace(b'\r\n',b'\n')
    entries[path]=(data,0o755 if '/uci-defaults/' in '/'+path else 0o644)
 return entries

def build():
 dist=ROOT/'dist'; dist.mkdir(exist_ok=True)
 stage=ROOT/'build'
 stage.mkdir(exist_ok=True)
 for name,(description,deps,license_id) in PACKAGES.items():
  entries=payload(name)
  data=tar_gz(entries)
  control=('Package: '+name+'\nVersion: '+VERSION+'-1\nArchitecture: all\n'
   +'Maintainer: OpenWrt Studio contributors\nSection: luci\nPriority: optional\n'
   +'Depends: '+', '.join(deps)+'\nLicense: '+license_id+'\n'
   +'Source: https://github.com/RichkovGit/OpenWrtStudio-LuCI\n'
   +'Installed-Size: '+str(len(gzip.decompress(data)))+'\nDescription: '+description+'\n')
  hooks={'postinst':(POST_THEME if name.startswith('luci-theme') else POST_APP).encode()}
  if name.startswith('luci-theme'):
   hooks['prerm']=PRE_REMOVE_THEME.encode()
  else:
   hooks['postrm']=POST_APP.encode()
  controls={'control':(control.encode(),0o644)}
  controls.update({k:(v,0o755) for k,v in hooks.items()})
  archive=tar_gz({'debian-binary':(b'2.0\n',0o644),'control.tar.gz':(tar_gz(controls),0o644),'data.tar.gz':(data,0o644)})
  output=dist/f'{name}_{VERSION}-1_all.ipk';output.write_bytes(archive)
  # Stage the exact same payload for the real APKv3 builder.
  package_stage=stage/name
  if package_stage.exists():
   if package_stage.resolve().parent != stage.resolve():raise ValueError('Unsafe stage path')
   def writable_retry(function,path,error):
    os.chmod(path,0o777)
    function(path)
   shutil.rmtree(package_stage,onexc=writable_retry)
  for path,(content,mode) in entries.items():
   target=package_stage/'files'/path;target.parent.mkdir(parents=True,exist_ok=True)
   target.write_bytes(content);target.chmod(mode)
  for hook,content in hooks.items():
   target=package_stage/hook;target.write_bytes(content);target.chmod(0o755)
  (package_stage/'metadata.json').write_text(json.dumps({'name':name,'version':VERSION+'-r1','depends':deps,'license':license_id}),encoding='utf-8')
  validate_ipk(output,entries)
 checksums()

def validate_ipk(path,expected=None):
 with tarfile.open(path,'r:gz') as outer:
  assert set(outer.getnames())=={'./debian-binary','./control.tar.gz','./data.tar.gz'}
  assert outer.extractfile('./debian-binary').read()==b'2.0\n'
  with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./control.tar.gz').read()),mode='r:gz') as ctrl:
   control=ctrl.extractfile('./control').read().decode()
   assert 'Architecture: all\n' in control and f'Version: {VERSION}-1\n' in control
   for member in ctrl:
    if member.name!='./control':assert member.mode==0o755
  with tarfile.open(fileobj=io.BytesIO(outer.extractfile('./data.tar.gz').read()),mode='r:gz') as data:
   actual={}
   for member in data:
    if member.isdir():
     assert member.mode==0o755
     continue
    assert member.isfile() and member.uid==0 and member.gid==0
    assert not member.name.startswith('/') and '..' not in Path(member.name).parts
    body=data.extractfile(member).read();actual[member.name[2:]]=(body,member.mode)
    if member.name.endswith('.json'):json.loads(body)
   if expected is not None:assert actual==expected
 print('Validated',path.name,len(actual),'payload files')

def checksums():
 lines=[]
 for file in sorted((ROOT/'dist').glob('*')):
  if file.suffix in ('.ipk','.apk'):
   lines.append(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.name+'\n')
 (ROOT/'dist'/'SHA256SUMS.txt').write_text(''.join(lines),encoding='ascii')

if __name__=='__main__':build()
