import QtQuick
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    // Parsed output of fetch_usage.py: { fetchedAt, providers: [...] }
    property var usage: null
    property bool loading: false
    property string lastError: ""

    // Smallest remaining percentage across every window of every provider,
    // or -1 when nothing is known yet. Drives the tray icon.
    readonly property real worstRemaining: {
        if (!usage || !usage.providers)
            return -1;
        let worst = -1;
        for (const p of usage.providers) {
            for (const w of (p.windows || [])) {
                const left = 100 - w.usedPercent;
                if (worst < 0 || left < worst)
                    worst = left;
            }
        }
        return worst;
    }

    readonly property string scriptPath: Qt.resolvedUrl("../code/fetch_usage.py").toString().replace(/^file:\/\//, "")
    readonly property string command: "python3 -I '" + scriptPath.replace(/'/g, "'\\''") + "'"

    function refresh() {
        if (loading)
            return;
        loading = true;
        executable.connectSource(command);
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source);
            root.loading = false;
            try {
                root.usage = JSON.parse(data["stdout"]);
                root.lastError = "";
            } catch (e) {
                root.lastError = (data["stderr"] || "").trim() || i18n("Yardımcı betik geçersiz çıktı verdi");
            }
        }
    }

    Timer {
        interval: 5 * 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    // Refresh whenever the popup opens so the numbers are current on click.
    onExpandedChanged: if (expanded) refresh()

    Plasmoid.icon: "speedometer"
    toolTipMainText: i18n("Ajan Limitleri")
    toolTipSubText: {
        if (!usage || !usage.providers)
            return loading ? i18n("Yükleniyor…") : lastError;
        return usage.providers.map(p => {
            const parts = (p.windows || []).filter(w => w.key === "session" || w.key === "weekly")
                .map(w => w.label + " %" + Math.round(100 - w.usedPercent));
            return p.name + ": " + (parts.length ? parts.join(", ") : (p.error || "?"));
        }).join("\n");
    }

    switchWidth: Kirigami.Units.gridUnit * 12
    switchHeight: Kirigami.Units.gridUnit * 12

    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}
}
