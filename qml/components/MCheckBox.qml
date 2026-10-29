import QtQuick
import QtQuick.Controls

// Чекбокс в стиле темы: индикатор рисуем сами, иначе Basic-стиль даёт чёрный квадрат.
CheckBox {
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
        radius: 3
        color: control.checked ? Theme.colors.primary : "transparent"
        border.width: 2
        border.color: control.checked ? Theme.colors.primary : Theme.colors.outline

        Text {
            visible: control.checked
            anchors.centerIn: parent
            text: "✓"
            color: Theme.colors.on_primary
            font.pixelSize: 12
            font.weight: Font.Bold
        }
    }
}
