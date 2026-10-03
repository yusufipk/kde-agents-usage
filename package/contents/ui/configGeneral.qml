import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3

KCM.SimpleKCM {
    id: root

    // Each box is the config key itself. The dialog reads these back to decide
    // whether anything changed, and copies them into the config on Apply, so a
    // box that is not aliased to its key never marks the page dirty.
    property alias cfg_showClaude: claudeBox.checked
    property alias cfg_showCodex: codexBox.checked
    property alias cfg_showOpenCode: opencodeBox.checked

    // The dialog also hands over a default per key, for its "reset" button, and
    // warns about every key it cannot find a home for.
    property bool cfg_showClaudeDefault: true
    property bool cfg_showCodexDefault: true
    property bool cfg_showOpenCodeDefault: true

    Kirigami.FormLayout {
        Layout.fillWidth: true

        QQC2.CheckBox {
            id: claudeBox

            Kirigami.FormData.label: i18n("Claude:")
            text: i18n("Limits and tokens")
        }

        QQC2.CheckBox {
            id: codexBox

            Kirigami.FormData.label: i18n("Codex:")
            text: i18n("Limits and tokens")
        }

        QQC2.CheckBox {
            id: opencodeBox

            Kirigami.FormData.label: i18n("OpenCode:")
            text: i18n("Go limits and tokens")
        }

        PlasmaComponents3.Label {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.smallSpacing
            Layout.columnSpan: 2
            text: i18n("A switched-off agent takes no room in either tab and is left out of the tray icon.")
            color: Kirigami.Theme.disabledTextColor
            font: Kirigami.Theme.smallFont
            wrapMode: Text.Wrap
        }
    }
}
