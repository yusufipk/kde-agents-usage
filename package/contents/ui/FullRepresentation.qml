pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras

PlasmaExtras.Representation {
    id: full

    readonly property var providers: (root.usage && root.usage.providers) ? root.usage.providers : []
    readonly property bool hasData: providers.length > 0
    // Wall clock used for relative reset times; bumped by the timer below.
    property real now: Date.now()

    Layout.minimumWidth: Kirigami.Units.gridUnit * 18
    Layout.preferredWidth: Kirigami.Units.gridUnit * 20
    Layout.maximumWidth: Kirigami.Units.gridUnit * 28
    Layout.minimumHeight: Kirigami.Units.gridUnit * 12
    Layout.preferredHeight: Math.min(Kirigami.Units.gridUnit * 32, implicitHeight)

    collapseMarginsHint: true
    focus: true
    Keys.onEscapePressed: root.expanded = false

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

    function resetText(iso) {
        if (!iso)
            return "";
        const t = Date.parse(iso);
        if (isNaN(t))
            return "";
        const diff = t - now;
        if (diff <= 0)
            return i18n("Sıfırlandı, yenileniyor");
        const totalMinutes = Math.ceil(diff / 60000);
        const days = Math.floor(totalMinutes / 1440);
        const hours = Math.floor((totalMinutes % 1440) / 60);
        const minutes = totalMinutes % 60;
        if (days > 0)
            return i18n("%1 gün %2 sa sonra sıfırlanır", days, hours);
        if (hours > 0)
            return i18n("%1 sa %2 dk sonra sıfırlanır", hours, minutes);
        return i18n("%1 dk sonra sıfırlanır", minutes);
    }

    function fetchedText() {
        if (!root.usage || !root.usage.fetchedAt)
            return "";
        const t = Date.parse(root.usage.fetchedAt);
        if (isNaN(t))
            return "";
        return i18n("Son güncelleme: %1", Qt.formatTime(new Date(t), "HH:mm"));
    }

    Timer {
        interval: 30 * 1000
        running: root.expanded
        repeat: true
        triggeredOnStart: true
        onTriggered: full.now = Date.now()
    }

    Connections {
        target: root
        function onUsageChanged() {
            full.now = Date.now();
        }
    }

    header: PlasmaExtras.PlasmoidHeading {
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaExtras.Heading {
                Layout.fillWidth: true
                level: 1
                text: i18n("Ajan Limitleri")
                elide: Text.ElideRight
            }

            PlasmaComponents3.BusyIndicator {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                visible: root.loading
                running: visible
            }

            PlasmaComponents3.ToolButton {
                visible: !root.loading
                icon.name: "view-refresh"
                text: i18n("Yenile")
                display: PlasmaComponents3.AbstractButton.IconOnly
                onClicked: root.refresh()

                PlasmaComponents3.ToolTip.text: text
                PlasmaComponents3.ToolTip.visible: hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
            }
        }
    }

    contentItem: PlasmaComponents3.ScrollView {
        id: scroll

        contentWidth: availableWidth
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        Item {
            width: scroll.availableWidth
            implicitHeight: column.implicitHeight + Kirigami.Units.largeSpacing * 2

            ColumnLayout {
                id: column

                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: Kirigami.Units.largeSpacing
                }
                spacing: Kirigami.Units.smallSpacing

                // First fetch still running
                PlasmaExtras.PlaceholderMessage {
                    Layout.fillWidth: true
                    Layout.topMargin: Kirigami.Units.gridUnit * 2
                    Layout.bottomMargin: Kirigami.Units.gridUnit * 2
                    visible: !full.hasData && root.loading
                    text: i18n("Limitler alınıyor…")

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
                    visible: !full.hasData && !root.loading
                    iconName: root.lastError ? "data-error" : "speedometer"
                    text: root.lastError ? i18n("Limitler alınamadı") : i18n("Henüz veri yok")
                    explanation: root.lastError
                    helpfulAction: QQC2.Action {
                        icon.name: "view-refresh"
                        text: i18n("Yeniden dene")
                        onTriggered: root.refresh()
                    }
                }

                Repeater {
                    model: full.providers

                    delegate: ColumnLayout {
                        id: providerItem

                        required property var modelData
                        required property int index
                        readonly property var windows: modelData.windows || []
                        readonly property string staleHint: modelData.error || i18n("Canlı veri alınamadı, önbellekteki değerler gösteriliyor")

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
                                text: providerItem.modelData.name || providerItem.modelData.id
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
                                    text: i18n("eski veri")
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
                                source: providerItem.modelData.error ? "data-error" : "data-information"
                                color: providerItem.modelData.error ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.disabledTextColor
                            }

                            PlasmaComponents3.Label {
                                Layout.fillWidth: true
                                text: providerItem.modelData.error || i18n("Bu dönemde henüz kullanım yok")
                                color: Kirigami.Theme.disabledTextColor
                                wrapMode: Text.Wrap
                            }
                        }

                        Repeater {
                            model: providerItem.windows

                            delegate: ColumnLayout {
                                id: windowItem

                                required property var modelData
                                readonly property int remaining: full.remainingOf(modelData)
                                readonly property color tone: full.tone(remaining)
                                readonly property string reset: full.resetText(modelData.resetsAt)

                                Layout.fillWidth: true
                                Layout.topMargin: Kirigami.Units.smallSpacing
                                spacing: Math.round(Kirigami.Units.smallSpacing / 2)

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Kirigami.Units.smallSpacing

                                    PlasmaComponents3.Label {
                                        Layout.fillWidth: true
                                        Layout.alignment: Qt.AlignBaseline
                                        text: windowItem.modelData.label || windowItem.modelData.key
                                        elide: Text.ElideRight
                                    }

                                    PlasmaExtras.Heading {
                                        Layout.alignment: Qt.AlignBaseline
                                        level: 2
                                        text: i18nc("remaining percentage", "%%1", windowItem.remaining)
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
        }
    }

    footer: PlasmaExtras.PlasmoidHeading {
        position: QQC2.ToolBar.Footer
        visible: full.hasData

        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents3.Label {
                Layout.fillWidth: true
                text: full.fetchedText()
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
            }

            PlasmaComponents3.Label {
                // fetchedAt is the run time, so a stale provider also means the refresh failed
                readonly property var staleErrors: (root.usage && root.usage.providers || []).filter(p => p.stale).map(p => p.name + ": " + p.error)
                visible: (root.lastError.length > 0 || staleErrors.length > 0) && full.hasData
                text: i18n("Yenileme başarısız")
                color: Kirigami.Theme.negativeTextColor
                font: Kirigami.Theme.smallFont

                HoverHandler {
                    id: errorHover
                }

                PlasmaComponents3.ToolTip.text: [root.lastError].concat(staleErrors).filter(t => t).join("\n")
                PlasmaComponents3.ToolTip.visible: errorHover.hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
            }
        }
    }
}
