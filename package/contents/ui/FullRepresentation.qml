pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras

PlasmaExtras.Representation {
    id: full

    // Remembered for the session only
    property int currentTab: 0
    readonly property bool limitsTab: currentTab === 0
    readonly property bool busy: limitsTab ? root.loading : root.tokensLoading

    Layout.minimumWidth: Kirigami.Units.gridUnit * 18
    Layout.preferredWidth: Kirigami.Units.gridUnit * 20
    Layout.maximumWidth: Kirigami.Units.gridUnit * 28
    Layout.minimumHeight: Kirigami.Units.gridUnit * 12
    Layout.preferredHeight: Math.min(Kirigami.Units.gridUnit * 36, implicitHeight)

    collapseMarginsHint: true
    focus: true
    Keys.onEscapePressed: root.expanded = false

    function refreshCurrent() {
        if (limitsTab)
            root.refresh();
        else
            root.refreshTokens();
    }

    header: PlasmaExtras.PlasmoidHeading {
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents3.TabBar {
                id: tabBar
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: full.currentTab
                onCurrentIndexChanged: full.currentTab = currentIndex

                PlasmaComponents3.TabButton {
                    text: i18n("Limits")
                }

                PlasmaComponents3.TabButton {
                    text: i18n("Tokens")
                }
            }

            PlasmaComponents3.BusyIndicator {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                visible: full.busy
                running: visible
            }

            PlasmaComponents3.ToolButton {
                visible: !full.busy
                icon.name: "view-refresh"
                text: i18n("Refresh")
                display: PlasmaComponents3.AbstractButton.IconOnly
                onClicked: full.refreshCurrent()

                PlasmaComponents3.ToolTip.text: text
                PlasmaComponents3.ToolTip.visible: hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
            }

            // Tray widgets have a short context menu that is easy to miss, and
            // which only offers the settings once the pointer is over the icon,
            // so the popup carries its own way in.
            PlasmaComponents3.ToolButton {
                icon.name: "configure"
                text: i18n("Settings")
                display: PlasmaComponents3.AbstractButton.IconOnly
                onClicked: root.openConfig()

                PlasmaComponents3.ToolTip.text: text
                PlasmaComponents3.ToolTip.visible: hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
            }
        }
    }

    contentItem: PlasmaComponents3.ScrollView {
        id: scroll

        // Fixed implicit width: deriving it from contentWidth loops through availableWidth.
        implicitWidth: Kirigami.Units.gridUnit * 20
        contentWidth: availableWidth
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        Item {
            width: scroll.availableWidth
            implicitHeight: (full.limitsTab ? limitsView.implicitHeight : tokensView.implicitHeight) + Kirigami.Units.largeSpacing * 2

            LimitsView {
                id: limitsView

                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: Kirigami.Units.largeSpacing
                }
                visible: full.limitsTab
                usage: root.usage
                loading: root.loading
                lastError: root.lastError
                visibleProviders: root.visibleProviders
                active: root.expanded
                onRefreshRequested: root.refresh()
            }

            TokensView {
                id: tokensView

                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: Kirigami.Units.largeSpacing
                }
                visible: !full.limitsTab
                tokens: root.tokens
                loading: root.tokensLoading
                error: root.tokensError
                visibleProviders: root.visibleProviders
                onRefreshRequested: root.refreshTokens()
            }
        }
    }

    footer: PlasmaExtras.PlasmoidHeading {
        position: QQC2.ToolBar.Footer
        visible: full.limitsTab ? limitsView.hasData : tokensView.hasData

        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents3.Label {
                Layout.fillWidth: true
                visible: full.limitsTab
                text: limitsView.fetchedText
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
            }

            PlasmaComponents3.Label {
                Layout.fillWidth: true
                visible: !full.limitsTab
                text: i18n("From local Claude Code, Codex and OpenCode logs; claude.ai chats are not included.")
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
                wrapMode: Text.Wrap
            }

            PlasmaComponents3.Label {
                readonly property var errors: full.limitsTab ? [root.lastError].concat(limitsView.staleErrors) : [root.tokensError]
                readonly property string errorHint: errors.filter(t => t).map(t => limitsView.plain(t)).join("\n")

                visible: errorHint.length > 0
                text: i18n("Refresh failed")
                color: Kirigami.Theme.negativeTextColor
                font: Kirigami.Theme.smallFont

                HoverHandler {
                    id: errorHover
                }

                PlasmaComponents3.ToolTip.text: errorHint
                PlasmaComponents3.ToolTip.visible: errorHover.hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
            }
        }
    }
}
