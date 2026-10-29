import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Проверка сетевого соединения: шлюз, интернет, YouTube, Discord, Telegram.
Item {
    id: page

    signal back()

    readonly property var lines: Backend.checkLog
    readonly property bool running: Backend.checkRunning

    function kindColor(kind) {
        switch (kind) {
        case "success": return Theme.colors.success
        case "warning": return Theme.colors.warning
        case "error": return Theme.colors.error
        case "primary": return Theme.colors.primary
        default: return Theme.colors.on_surface
        }
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: focusTimer.start()

    onVisibleChanged: if (visible) focusTimer.start()

    Timer {
        id: focusTimer
        interval: 60
        onTriggered: backButton.forceActiveFocus()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaces.xl
        anchors.rightMargin: Theme.spaces.xl
        anchors.topMargin: Theme.spaces.lg
        anchors.bottomMargin: Theme.spaces.lg
        spacing: Theme.spaces.md

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                id: backButton
                variant: "icon"
                iconSource: "../icons/back.svg"
                onClicked: page.back()
                ToolTip.visible: hovered
                ToolTip.text: "Назад"
                KeyNavigation.down: startButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Проверка соединения"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Шлюз, интернет, YouTube, Discord и Telegram"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }

            BusyIndicator {
                running: page.running
                visible: page.running
                implicitWidth: 22
                implicitHeight: 22
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                id: startButton
                text: page.running ? "Остановить" : "Проверить"
                variant: "filled"
                iconSource: page.running ? "../icons/close.svg" : "../icons/play.svg"
                onClicked: page.running ? Backend.stopConnectionCheck()
                                        : Backend.startConnectionCheck()
                KeyNavigation.right: clearButton
            }

            MButton {
                id: clearButton
                text: "Очистить"
                variant: "outlined"
                enabled: !page.running && page.lines.length > 0
                onClicked: Backend.clearConnectionCheck()
                KeyNavigation.left: startButton
            }

            Text {
                Layout.fillWidth: true
                text: Backend.checkSummary.length > 0 ? Backend.checkSummary
                    : (page.running ? "Проверка выполняется…" : "Проверки выполняются по очереди")
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                elide: Text.ElideRight
            }
        }

        // Результаты по сервисам.
        RowLayout {
            Layout.fillWidth: true
            visible: Object.keys(Backend.checkServices).length > 0
            spacing: Theme.spaces.sm

            Repeater {
                model: [
                    { key: "youtube", title: "YouTube" },
                    { key: "discord", title: "Discord" },
                    { key: "telegram", title: "Telegram" }
                ]

                delegate: Rectangle {
                    readonly property var value: Backend.checkServices[modelData.key]

                    Layout.fillWidth: true
                    implicitHeight: 52
                    radius: Theme.radii.md
                    color: value === true ? Theme.colors.success_container
                        : value === false ? Theme.colors.error_container
                        : Theme.colors.surface_container_high

                    Text {
                        anchors.centerIn: parent
                        text: modelData.title + ": " + (value === true ? "доступен"
                            : value === false ? "недоступен" : "не проверялся")
                        color: value === true ? Theme.colors.on_success_container
                            : value === false ? Theme.colors.on_error_container
                            : Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                    }
                }
            }
        }

        // Лог проверок.
        Flickable {
            id: logFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: logColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            onContentHeightChanged: contentY = Math.max(0, contentHeight - height)

            Rectangle {
                anchors.fill: parent
                radius: Theme.radii.md
                color: Theme.colors.surface_container_low
                border.width: 1
                border.color: Theme.colors.outline_variant
            }

            Column {
                id: logColumn

                width: logFlick.width - Theme.spaces.md * 2
                x: Theme.spaces.md
                y: Theme.spaces.md
                spacing: 2

                Repeater {
                    model: page.lines

                    delegate: Text {
                        width: logColumn.width
                        text: modelData.text
                        color: page.kindColor(modelData.kind)
                        font.family: "monospace"
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }
                }

                Text {
                    visible: page.lines.length === 0
                    text: "Нажмите «Проверить», чтобы запустить проверки."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                }
            }
        }
    }
}
