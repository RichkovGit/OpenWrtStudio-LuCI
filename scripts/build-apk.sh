#!/bin/sh
# Run after python3 scripts/build-packages.py. Requires apk-tools v3 with mkpkg.
set -eu
cd "$(dirname "$0")/.."
APK=${APK:-apk}
mkdir -p dist
for name in luci-theme-openwrtstudio luci-app-openwrtstudio; do
 stage="build/$name"
 find "$stage" -type d -exec chmod 755 {} \;
 find "$stage/files" -type f -exec chmod 644 {} \;
 if [ -d "$stage/files/etc/uci-defaults" ]; then chmod 755 "$stage/files/etc/uci-defaults/"*; fi
 if [ "$name" = luci-theme-openwrtstudio ]; then
  deps='luci-theme-bootstrap'
  license='MIT AND Apache-2.0'
  set -- --script "pre-deinstall:$stage/prerm"
 else
  deps='luci-base luci-mod-status luci-mod-network luci-mod-system luci-app-firewall'
  license=MIT
  set -- --script "post-deinstall:$stage/postrm"
 fi
 "$APK" mkpkg --compat 3.0.0 --files "$stage/files" \
  --info "name:$name" --info 'version:0.1.1-r1' --info 'arch:noarch' \
  --info "description:OpenWrt Studio LuCI preview" \
  --info 'url:https://github.com/RichkovGit/OpenWrtStudio-LuCI' \
  --info "license:$license" --info "depends:$deps" \
  --script "post-install:$stage/postinst" --script "post-upgrade:$stage/postinst" \
  "$@" --output "dist/$name-0.1.1-r1.apk"
done
