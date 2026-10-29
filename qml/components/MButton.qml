import QtQuick
import QtQuick.Controls

// Кнопка Material 3: filled / tonal / outlined / text / icon.
Button {
    id: control

    property string variant: "filled"
    property url iconSource
    property bool dense: false

    readonly property string _bgRole: variant === "filled" ? "primary"
        : variant === "tonal" ? "secondary_container"
        : variant === "elevated" ? "surface_container_high"
        : "transparent"
    readonly property string _fgRole: variant === "filled" ? "on_primary"
        : variant === "tonal" ? "on_secondary_container"
        : "primary"
    readonly property color _contentColor: enabled ? Theme.colors[_fgRole]
        : Theme.colors.on_surface_variant
    readonly property bool _hasIcon: iconSource.toString().length > 0

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: dense ? 34 : 40
    implicitWidth: Math.max(dense ? 34 : 72,
        contentItem.implicitWidth + leftPadding + rightPadding)
    leftPadding: dense ? 8 : (text.length === 0 ? 10 : 18)
    rightPadding: leftPadding
    topPadding: dense ? 6 : 10
    bottomPadding: topPadding
    font.pixelSize: Theme.typeSize("label_large")
    font.weight: Font.Medium

    contentItem: Row {
        spacing: control.text.length > 0 && control._hasIcon ? Theme.spaces.sm : 0
        anchors.centerIn: parent

        MIcon {
            visible: control._hasIcon
            anchors.verticalCenter: parent.verticalCenter
            source: control.iconSource
            size: control.dense ? 16 : 18
            color: control._contentColor
            opacity: control.enabled ? 1 : 0.38
        }

        Text {
            visible: control.text.length > 0
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            font: control.font
            color: control._contentColor
            opacity: control.enabled ? 1 : 0.38
        }
    }

    background: Rectangle {
        radius: Theme.radii.full
        color: control._bgRole === "transparent" ? "transparent" : Theme.colors[control._bgRole]
        border.width: control.variant === "outlined" ? 1 : 0
        border.color: Theme.colors.outline
        opacity: control.enabled ? 1 : 0.5

        // State layer (hover/press) по M3.
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: Theme.colors[control._fgRole]
            opacity: !control.enabled ? 0
                : control.pressed ? Theme.stateOpacity.press
                : control.hovered ? Theme.stateOpacity.hover
                : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durations.short } }
        }

        // Фокус-рамка: навигация клавиатурой и геймпадом.
        Rectangle {
            anchors.fill: parent
            anchors.margins: -2
            radius: parent.radius + 2
            color: "transparent"
            border.width: 2
            border.color: Theme.colors.primary
            visible: control.activeFocus && control.visualFocus
        }
    }
}
