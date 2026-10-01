#!/bin/sh
# Install or upgrade the plasmoid for the current user. After an upgrade, restart
# plasmashell (systemctl --user restart plasma-plasmashell) to load the new QML.
set -e
cd "$(dirname "$0")"
if kpackagetool6 -t Plasma/Applet -l | grep -q io.github.yusufipk.agentsusage; then
    kpackagetool6 -t Plasma/Applet -u package
else
    kpackagetool6 -t Plasma/Applet -i package
fi
echo "Installed. Add 'Agents Usage' to a panel, or enable it under System Tray settings."
