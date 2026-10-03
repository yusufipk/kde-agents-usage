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

    // Which agents the settings let through, as ids used by both tabs. The
    // settings object is only there once the config UI has loaded, and a key
    // left at its schema default reads true, so anything unknown counts as on.
    readonly property var agents: [
        { id: "claude", on: configEnabled("showClaude") },
        { id: "codex", on: configEnabled("showCodex") },
        { id: "opencode", on: configEnabled("showOpenCode") }
    ]
    readonly property var visibleProviders: agents.filter(a => a.on).map(a => a.id)

    function configEnabled(key) {
        const cfg = Plasmoid.configuration;
        return !cfg || cfg[key] !== false;
    }

    // Plasma 6 has no Plasmoid.showConfig(); the shell opens the dialog when the
    // applet's own "configure" action fires, which is the same one its context
    // menu carries. It is absent while the applet is still starting up.
    function openConfig() {
        const action = plasmoid.internalAction("configure");
        if (action)
            action.trigger();
    }

    function isVisible(id) {
        return visibleProviders.indexOf(id) >= 0;
    }

    // Smallest remaining percentage across every window of every provider,
    // or -1 when nothing is known yet. Drives the tray icon.
    readonly property real worstRemaining: {
        if (!usage || !usage.providers)
            return -1;
        let worst = -1;
        for (const p of usage.providers) {
            if (!root.isVisible(p.id))
                continue;
            for (const w of (p.windows || [])) {
                const left = 100 - w.usedPercent;
                if (worst < 0 || left < worst)
                    worst = left;
            }
        }
        return worst;
    }

    // Parsed output of token_stats.py: { generatedAt, scanning, daily, periods }
    property var tokens: null
    property bool tokensLoading: false
    property string tokensError: ""

    function scriptCommand(name, niced) {
        const path = Qt.resolvedUrl("../code/" + name).toString().replace(/^file:\/\//, "");
        // The token scan reads large log files, so it runs at idle priority.
        return (niced ? "nice -n 10 ionice -c 3 " : "") + "python3 -I '" + path.replace(/'/g, "'\\''") + "'";
    }
    readonly property string command: scriptCommand("fetch_usage.py", false)
    readonly property string tokensCommand: scriptCommand("token_stats.py", true)

    function refreshTokens() {
        if (tokensLoading)
            return;
        tokensLoading = true;
        tokenSource.connectSource(tokensCommand);
    }

    P5Support.DataSource {
        id: tokenSource
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source);
            root.tokensLoading = false;
            try {
                root.tokens = JSON.parse(data["stdout"]);
                root.tokensError = "";
            } catch (e) {
                root.tokensError = (data["stderr"] || "").trim() || i18n("The helper script returned invalid output");
            }
        }
    }

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
                root.lastError = (data["stderr"] || "").trim() || i18n("The helper script returned invalid output");
            }
        }
    }

    Timer {
        interval: 5 * 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            root.refresh();
            root.refreshTokens();
        }
    }

    // Refresh whenever the popup opens so the numbers are current on click.
    onExpandedChanged: {
        if (expanded) {
            refresh();
            refreshTokens();
        }
    }

    Plasmoid.icon: "speedometer"
    toolTipMainText: i18n("Agents Usage")
    toolTipSubText: {
        if (!usage || !usage.providers)
            return loading ? i18n("Loading…") : lastError;
        const rows = usage.providers.filter(p => root.isVisible(p.id)).map(p => {
            const parts = (p.windows || []).filter(w => w.key === "session" || w.key === "weekly" || w.key === "monthly")
                .map(w => i18nc("tooltip, e.g. 5 hours: 76% left", "%1: %2% left", w.key === "session" ? i18n("5 hours") : (w.key === "weekly" ? i18n("Weekly") : i18n("Monthly")), Math.round(100 - w.usedPercent)));
            return p.name + ": " + (parts.length ? parts.join(", ") : (p.errorCode ? i18n("unavailable") : i18n("no data")));
        });
        return rows.length ? rows.join("\n") : i18n("No data yet");
    }

    switchWidth: Kirigami.Units.gridUnit * 12
    switchHeight: Kirigami.Units.gridUnit * 12

    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}
}
