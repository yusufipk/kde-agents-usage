import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami

// Panel / system tray icon: a ring showing the worst remaining quota.
MouseArea {
    id: compact

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property bool planar: Plasmoid.formFactor === PlasmaCore.Types.Planar
    readonly property real remaining: root.worstRemaining
    readonly property bool known: remaining >= 0
    readonly property real fraction: known ? Math.max(0, Math.min(1, remaining / 100)) : 0
    readonly property color tone: {
        if (!known)
            return Kirigami.Theme.disabledTextColor;
        if (remaining < 15)
            return Kirigami.Theme.negativeTextColor;
        if (remaining <= 40)
            return Kirigami.Theme.neutralTextColor;
        return Kirigami.Theme.highlightColor;
    }
    readonly property real size: Math.min(width, height)

    Layout.minimumWidth: vertical ? Kirigami.Units.iconSizes.small : height
    Layout.minimumHeight: vertical ? width : Kirigami.Units.iconSizes.small
    Layout.preferredWidth: planar ? Kirigami.Units.iconSizes.large : (vertical ? -1 : height)
    Layout.preferredHeight: planar ? Kirigami.Units.iconSizes.large : (vertical ? width : -1)

    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.MiddleButton
    onClicked: mouse => {
        if (mouse.button === Qt.MiddleButton)
            root.refresh();
        else
            root.expanded = !root.expanded;
    }

    Accessible.role: Accessible.Button
    Accessible.name: root.toolTipMainText

    // Fallback glyph while nothing is known yet.
    Kirigami.Icon {
        anchors.fill: parent
        visible: !compact.known
        source: "speedometer"
        active: compact.containsMouse
    }

    Shape {
        id: ring

        readonly property real stroke: Math.max(2, Math.round(width / 9))
        readonly property real radius: width / 2 - stroke / 2
        readonly property real inner: width - stroke * 2

        anchors.centerIn: parent
        width: compact.size
        height: width
        visible: compact.known
        antialiasing: true
        preferredRendererType: Shape.CurveRenderer
        opacity: compact.containsMouse ? 1 : 0.9

        Behavior on opacity {
            NumberAnimation { duration: Kirigami.Units.shortDuration }
        }

        // Track
        ShapePath {
            strokeColor: Qt.alpha(Kirigami.Theme.textColor, 0.25)
            strokeWidth: ring.stroke
            fillColor: "transparent"
            capStyle: ShapePath.FlatCap

            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.radius
                radiusY: ring.radius
                startAngle: -90
                sweepAngle: 360
            }
        }

        // Remaining share, clockwise from the top
        ShapePath {
            strokeColor: compact.tone
            strokeWidth: ring.stroke
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.radius
                radiusY: ring.radius
                startAngle: -90
                sweepAngle: 360 * compact.fraction

                Behavior on sweepAngle {
                    NumberAnimation {
                        duration: Kirigami.Units.longDuration
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }

        // Number when there is room, otherwise a status dot.
        PlasmaComponents3.Label {
            anchors.centerIn: parent
            visible: ring.inner >= 18
            text: Math.round(compact.remaining)
            font.pixelSize: Math.max(6, Math.round(ring.inner * 0.5))
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        Rectangle {
            anchors.centerIn: parent
            visible: ring.inner < 18
            width: Math.max(2, Math.round(ring.inner * 0.35))
            height: width
            radius: width / 2
            color: compact.tone
        }
    }
}
