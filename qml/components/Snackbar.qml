import QtQuick

// Всплывающая статусная строка (Material 3 snackbar).
Rectangle {
    id: root

    property string text: ""
    property string kind: "info"
    property int duration: 5000

    function show(message, messageKind) {
        root.kind = messageKind && messageKind.length > 0 ? messageKind : "info"
        root.text = message
        hideTimer.restart()
    }

    implicitHeight: 48
    radius: Theme.radii.sm
    color: root.kind === "error" ? Theme.colors.error_container
        : root.kind === "success" ? Theme.colors.success_container
        : root.kind === "warning" ? Theme.colors.warning_container
        : Theme.colors.surface_container_highest
    border.width: 1
    border.color: Theme.colors.outline_variant

    opacity: root.text.length > 0 ? 1 : 0
    visible: opacity > 0.01
    Behavior on opacity { NumberAnimation { duration: Theme.durations.medium } }

    Text {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaces.lg
        anchors.rightMargin: Theme.spaces.lg
        verticalAlignment: Text.AlignVCenter
        text: root.text
        wrapMode: Text.WordWrap
        maximumLineCount: 3
        elide: Text.ElideRight
        color: root.kind === "error" ? Theme.colors.on_error_container
            : root.kind === "success" ? Theme.colors.on_success_container
            : root.kind === "warning" ? Theme.colors.on_warning_container
            : Theme.colors.on_surface
        font.pixelSize: Theme.typeSize("body_medium")
    }

    Timer {
        id: hideTimer
        interval: root.duration
        onTriggered: root.text = ""
    }
}
