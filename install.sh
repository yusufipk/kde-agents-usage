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
if kpackagetool6 -t Plasma/Applet -l | grep -q io.github.yusufipk.agentsusage; then
    kpackagetool6 -t Plasma/Applet -u package
else
    kpackagetool6 -t Plasma/Applet -i package
fi
echo "Installed. Add 'Agents Usage' to a panel, or enable it under System Tray settings."
