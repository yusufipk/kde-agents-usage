#!/bin/sh
# Install or upgrade the plasmoid for the current user. After an upgrade, restart
# plasmashell (systemctl --user restart plasma-plasmashell) to load the new QML.
set -e
cd "$(dirname "$0")"

# Compile translations into the package; without gettext the UI stays English.
if command -v msgfmt >/dev/null; then
    for po in po/*.po; do
        lang=$(basename "$po" .po)
        mkdir -p "package/contents/locale/$lang/LC_MESSAGES"
        msgfmt -o "package/contents/locale/$lang/LC_MESSAGES/plasma_applet_io.github.yusufipk.agentsusage.mo" "$po"
    done
fi
# An upgrade copies the files in place instead of running kpackagetool6 -u:
# that announces an uninstall over D-Bus, and the system tray answers it by
# dropping the widget from its "Always shown" list.
id=io.github.yusufipk.agentsusage
dest="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$id"
if [ -f "$dest/metadata.json" ]; then
    if command -v rsync >/dev/null; then
        rsync -a --delete package/ "$dest/"
    else
        cp -R package/. "$dest/"
    fi
else
    kpackagetool6 -t Plasma/Applet -i package
fi
echo "Installed. Add 'Agents Usage' to a panel, or enable it under System Tray settings."
