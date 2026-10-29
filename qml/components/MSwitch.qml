import QtQuick
import QtQuick.Controls

// Переключатель в стиле темы.
Switch {
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
        implicitWidth: 40
        implicitHeight: 22
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: height / 2
        color: control.checked ? Theme.colors.primary : Theme.colors.surface_container_highest
        border.width: 1
        border.color: control.checked ? Theme.colors.primary : Theme.colors.outline

        Rectangle {
            width: 16
            height: 16
            radius: width / 2
            y: (parent.height - height) / 2
            x: control.checked ? parent.width - width - 3 : 3
            color: control.checked ? Theme.colors.on_primary : Theme.colors.on_surface_variant
            Behavior on x { NumberAnimation { duration: Theme.durations.short } }
        }
    }
}
