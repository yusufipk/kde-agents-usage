pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras

// Remaining quota per provider: one meter per window.
ColumnLayout {
    id: limits

    // Parsed output of fetch_usage.py, see main.qml
    property var usage: null
    property bool loading: false
    property string lastError: ""
    // True while the popup is open; drives the relative-time clock
    property bool active: false
    signal refreshRequested()

    readonly property var providers: (usage && Array.isArray(usage.providers)) ? usage.providers.filter(p => !!p) : []
    readonly property bool hasData: providers.length > 0
    // Wall clock used for relative reset times; bumped by the timer below.
    property real now: Date.now()

    readonly property string fetchedText: {
        if (!usage || !usage.fetchedAt)
            return "";
        const t = Date.parse(usage.fetchedAt);
        if (isNaN(t))
            return "";
        return i18n("Last updated: %1", Qt.locale().toString(new Date(t), Locale.ShortFormat));
    }

    // fetchedAt is the run time, so a stale provider also means the refresh failed
    readonly property var staleErrors: providers.filter(p => p.stale).map(p => i18nc("provider name: error message", "%1: %2", limits.plain(p.name || p.id), limits.plain(p.errorCode ? limits.errorText(p.errorCode, p.errorDetail) : i18n("Live data unavailable, showing cached values"))))

    spacing: Kirigami.Units.smallSpacing

    // Attached tooltips and placeholder explanations have no textFormat hook,
    // so keep markup from helper output out of them.
    function plain(s) {
        return String(s === undefined || s === null ? "" : s).replace(/[<>]/g, "");
    }

    function remainingOf(w) {
        const used = Number(w.usedPercent);
        if (isNaN(used))
            return 0;
        return Math.max(0, Math.min(100, Math.round(100 - used)));
    }

    function tone(remaining) {
        if (remaining < 15)
            return Kirigami.Theme.negativeTextColor;
        if (remaining <= 40)
            return Kirigami.Theme.neutralTextColor;
        return Kirigami.Theme.highlightColor;
    }

    function windowLabel(w) {
        switch (w.key) {
        case "session":
            return i18n("5 hours");
        case "weekly":
            return i18n("Weekly");
        case "weekly_scoped":
            return w.model ? i18n("Weekly · %1", w.model) : i18n("Weekly");
        default:
            return w.key || "";
        }
    }

    function errorText(code, detail) {
        switch (code) {
        case "claude_no_login":
            return i18n("Claude Code login not found, sign in with claude");
        case "claude_expired":
            return i18n("Claude Code session expired, open claude once to renew it");
        case "claude_invalid":
            return i18n("Claude session is invalid, open claude once");
        case "codex_no_login":
            return i18n("Codex ChatGPT login not found, sign in with codex login");
        case "codex_refresh_failed":
            return i18n("Could not renew the Codex session, sign in again with codex login");
        case "codex_save_failed":
            return i18n("Could not save the renewed Codex session (%1), codex login may be needed", detail || "");
        case "http":
            return i18n("HTTP error %1", detail || "");
        case "network":
            return i18n("Connection error: %1", detail || "");
        case "bad_credentials":
            return i18n("Credentials file is unreadable or corrupt");
        default:
            return detail || i18n("Unknown error");
        }
    }

    function resetText(iso) {
        if (!iso)
            return "";
        const t = Date.parse(iso);
        if (isNaN(t))
            return "";
        const diff = t - now;
        if (diff <= 0)
            return i18n("Reset, refreshing");
        const totalMinutes = Math.ceil(diff / 60000);
        const days = Math.floor(totalMinutes / 1440);
        const hours = Math.floor((totalMinutes % 1440) / 60);
        const minutes = totalMinutes % 60;
        if (days > 0)
            return i18n("resets in %1 d %2 h", days, hours);
        if (hours > 0)
            return i18n("resets in %1 h %2 min", hours, minutes);
        return i18n("resets in %1 min", minutes);
    }

    Timer {
        interval: 30 * 1000
        running: limits.active
        repeat: true
        triggeredOnStart: true
        onTriggered: limits.now = Date.now()
    }

    onUsageChanged: now = Date.now()

    // First fetch still running
    PlasmaExtras.PlaceholderMessage {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.gridUnit * 2
        Layout.bottomMargin: Kirigami.Units.gridUnit * 2
        visible: !limits.hasData && limits.loading
        text: i18n("Fetching limits…")

        PlasmaComponents3.BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: parent.visible
        }
    }

    // Nothing cached and the fetch failed
    PlasmaExtras.PlaceholderMessage {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.gridUnit * 2
        Layout.bottomMargin: Kirigami.Units.gridUnit * 2
        visible: !limits.hasData && !limits.loading
        iconName: limits.lastError ? "data-error" : "speedometer"
        text: limits.lastError ? i18n("Could not fetch limits") : i18n("No data yet")
        explanation: limits.plain(limits.lastError)
        helpfulAction: QQC2.Action {
            icon.name: "view-refresh"
            text: i18n("Try again")
            onTriggered: limits.refreshRequested()
        }
    }

    Repeater {
        model: limits.providers

        delegate: ColumnLayout {
            id: providerItem

            required property var modelData
            required property int index
            readonly property var windows: modelData.windows || []
            readonly property string errorMessage: modelData.errorCode ? limits.errorText(modelData.errorCode, modelData.errorDetail) : ""
            readonly property string staleHint: limits.plain(errorMessage) || i18n("Live data unavailable, showing cached values")

            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Separator {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                Layout.bottomMargin: Kirigami.Units.smallSpacing
                visible: providerItem.index > 0
            }

            // Provider title row: name, plan badge, stale hint
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PlasmaExtras.Heading {
                    level: 3
                    text: providerItem.modelData.name || providerItem.modelData.id || ""
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }

                Rectangle {
                    visible: !!providerItem.modelData.plan
                    implicitWidth: planLabel.implicitWidth + Kirigami.Units.smallSpacing * 2
                    implicitHeight: planLabel.implicitHeight + Kirigami.Units.smallSpacing
                    radius: height / 2
                    color: Qt.alpha(Kirigami.Theme.highlightColor, 0.15)
                    border.width: 1
                    border.color: Qt.alpha(Kirigami.Theme.highlightColor, 0.4)

                    PlasmaComponents3.Label {
                        id: planLabel
                        anchors.centerIn: parent
                        text: providerItem.modelData.plan || ""
                        textFormat: Text.PlainText
                        color: Kirigami.Theme.highlightColor
                        font: Kirigami.Theme.smallFont
                    }
                }

                Item {
                    Layout.fillWidth: true
                }

                RowLayout {
                    id: staleRow
                    visible: !!providerItem.modelData.stale
                    spacing: Math.round(Kirigami.Units.smallSpacing / 2)

                    Kirigami.Icon {
                        Layout.preferredWidth: Kirigami.Units.iconSizes.small
                        Layout.preferredHeight: Kirigami.Units.iconSizes.small
                        source: "data-warning"
                        color: Kirigami.Theme.neutralTextColor
                    }

                    PlasmaComponents3.Label {
                        text: i18nc("cached values are shown", "stale")
                        color: Kirigami.Theme.disabledTextColor
                        font: Kirigami.Theme.smallFont
                    }

                    HoverHandler {
                        id: staleHover
                    }

                    PlasmaComponents3.ToolTip.text: providerItem.staleHint
                    PlasmaComponents3.ToolTip.visible: staleHover.hovered
                    PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
            }

            // Inline note when the provider returned no windows: an error, or simply no usage yet
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                visible: providerItem.windows.length === 0
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Icon {
                    Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                    Layout.alignment: Qt.AlignTop
                    source: providerItem.errorMessage ? "data-error" : "data-information"
                    color: providerItem.errorMessage ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.disabledTextColor
                }

                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    text: providerItem.errorMessage || i18n("No active limit window yet")
                    textFormat: Text.PlainText
                    color: Kirigami.Theme.disabledTextColor
                    wrapMode: Text.Wrap
                }
            }

            Repeater {
                model: providerItem.windows

                delegate: ColumnLayout {
                    id: windowItem

                    required property var modelData
                    readonly property int remaining: limits.remainingOf(modelData)
                    readonly property color tone: limits.tone(remaining)
                    readonly property string reset: limits.resetText(modelData.resetsAt)

                    Layout.fillWidth: true
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    spacing: Math.round(Kirigami.Units.smallSpacing / 2)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing

                        PlasmaComponents3.Label {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignBaseline
                            text: limits.windowLabel(windowItem.modelData)
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                        }

                        PlasmaExtras.Heading {
                            Layout.alignment: Qt.AlignBaseline
                            level: 2
                            text: i18nc("remaining percentage", "%1%", windowItem.remaining)
                            color: windowItem.remaining <= 40 ? windowItem.tone : Kirigami.Theme.textColor
                        }
                    }

                    // Meter: track plus a fill colored by how much is left
                    Rectangle {
                        id: meter
                        Layout.fillWidth: true
                        implicitHeight: Math.round(Kirigami.Units.smallSpacing * 1.5)
                        radius: height / 2
                        color: Qt.alpha(Kirigami.Theme.textColor, 0.15)

                        Rectangle {
                            anchors {
                                left: parent.left
                                top: parent.top
                                bottom: parent.bottom
                            }
                            width: Math.max(height, parent.width * windowItem.remaining / 100)
                            radius: parent.radius
                            color: windowItem.tone

                            Behavior on width {
                                NumberAnimation {
                                    duration: Kirigami.Units.longDuration
                                    easing.type: Easing.OutCubic
                                }
                            }
                        }
                    }

                    PlasmaComponents3.Label {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: windowItem.reset
                        color: Kirigami.Theme.disabledTextColor
                        font: Kirigami.Theme.smallFont
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
