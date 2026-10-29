import QtQuick
import QtQuick.Controls

// Радиокнопка в стиле темы.
RadioButton {
    id: control

    spacing: Theme.spaces.sm

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? Theme.colors.on_surface : Theme.colors.on_surface_variant
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        width: control.availableWidth
    }

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: control.checked ? Theme.colors.primary : Theme.colors.outline

        Rectangle {
            visible: control.checked
            anchors.centerIn: parent
            width: 9
            height: 9
            radius: width / 2
            color: Theme.colors.primary
        }
    }
}
