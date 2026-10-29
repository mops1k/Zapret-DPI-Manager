import QtQuick

// Карточка Material 3: заголовок, подзаголовок и произвольное содержимое.
Rectangle {
    id: root

    property string title: ""
    property string subtitle: ""
    property int padding: Theme.spaces.lg
    property int spacing: Theme.spaces.sm
    property bool outlined: true

    default property alias contentData: column.data

    color: Theme.colors.surface_container_low
    radius: Theme.radii.lg
    border.width: outlined ? 1 : 0
    border.color: Theme.colors.outline_variant
    implicitHeight: column.implicitHeight + padding * 2
    implicitWidth: column.implicitWidth + padding * 2

    Column {
        id: column
        x: root.padding
        y: root.padding
        width: root.width - root.padding * 2
        spacing: root.spacing

        Text {
            width: parent.width
            visible: root.title.length > 0
            text: root.title
            color: Theme.colors.on_surface
            font.pixelSize: Theme.typeSize("title_medium")
            font.weight: Font.Medium
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width
            visible: root.subtitle.length > 0
            text: root.subtitle
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_small")
            wrapMode: Text.WordWrap
        }
    }
}
