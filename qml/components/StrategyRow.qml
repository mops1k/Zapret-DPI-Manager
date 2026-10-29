import QtQuick
import QtQuick.Controls

// Строка списка стратегий: выбор, отметка рабочей, отметка текущей.
Button {
    id: row

    property string strategyName: ""
    property bool working: false
    property bool current: false
    property bool selected: false

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: 36
    implicitWidth: 220
    leftPadding: Theme.spaces.sm
    rightPadding: Theme.spaces.sm

    contentItem: Row {
        spacing: Theme.spaces.sm
        anchors.verticalCenter: parent.verticalCenter
        width: row.availableWidth

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: row.selected ? "◉" : "○"
            color: row.selected ? Theme.colors.primary : Theme.colors.on_surface_variant
            font.pixelSize: 15
            width: 18
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 18 - star.width - Theme.spaces.sm * 2
            text: row.strategyName
            elide: Text.ElideRight
            color: row.selected ? Theme.colors.primary : Theme.colors.on_surface
            font.pixelSize: Theme.typeSize("body_large")
            font.weight: row.current ? Font.DemiBold : Font.Normal
        }

        Text {
            id: star
            anchors.verticalCenter: parent.verticalCenter
            visible: row.working
            text: "★"
            color: Theme.colors.warning
            font.pixelSize: 13
            ToolTip.visible: hovered
            ToolTip.text: "Стратегия уже показала себя рабочей"
        }
    }

    background: Rectangle {
        radius: Theme.radii.md
        color: row.selected
            ? Qt.rgba(Theme.colors.primary.r, Theme.colors.primary.g,
                      Theme.colors.primary.b, 0.16)
            : row.hovered ? Theme.colors.surface_container_high
            : "transparent"
        border.width: row.activeFocus && row.visualFocus ? 2 : 0
        border.color: Theme.colors.primary
    }
}
