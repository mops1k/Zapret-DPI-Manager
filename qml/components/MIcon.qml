import QtQuick
import QtQuick.Effects

// Символическая иконка Breeze, перекрашенная в цвет темы.
Item {
    id: root

    property url source
    property color color: Theme.colors.on_surface
    property int size: 18

    implicitWidth: size
    implicitHeight: size

    Image {
        id: iconImage
        anchors.fill: parent
        source: root.source
        sourceSize: Qt.size(Math.round(root.size * 2), Math.round(root.size * 2))
        fillMode: Image.PreserveAspectFit
        smooth: true
        visible: false
    }

    MultiEffect {
        anchors.fill: iconImage
        source: iconImage
        colorization: 1.0
        colorizationColor: root.color
        visible: root.visible
    }
}
