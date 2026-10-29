import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Обновление: приложение, стратегии, движок nfqws.
Item {
    id: page

    signal back()

    readonly property var versions: Backend.updateVersions
    readonly property var lines: Backend.updateLog
    readonly property bool running: Backend.updateRunning

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshUpdateVersions()
        focusTimer.start()
    }

    onVisibleChanged: {
        if (visible) {
            Backend.refreshUpdateVersions()
            focusTimer.start()
        }
    }

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
                KeyNavigation.down: appButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Обновление"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Приложение, стратегии из Flowseal и движок nfqws"
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

        Card {
            Layout.fillWidth: true
            title: "Версии"
            subtitle: "Версия приложения и источников"

            Text {
                width: parent.width
                text: "Приложение: " + (page.versions.app || "—")
                    + " · стратегии: " + (page.versions.strategies || "—")
                    + " · движок: " + (page.versions.engine || "—")
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                text: "Обновление приложения перезапускает менеджер. Перед обновлением "
                    + "создаются резервные копии; для записи нужен пароль sudo."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                width: parent.width
                spacing: Theme.spaces.sm

                MButton {
                    id: appButton
                    text: "Обновить приложение"
                    variant: "filled"
                    enabled: !page.running
                    onClicked: Backend.startUpdate("app")
                    KeyNavigation.right: strategiesButton
                }

                MButton {
                    id: strategiesButton
                    text: "Обновить стратегии"
                    variant: "tonal"
                    enabled: !page.running
                    onClicked: Backend.startUpdate("strategies")
                    KeyNavigation.left: appButton
                    KeyNavigation.right: engineButton
                }

                MButton {
                    id: engineButton
                    text: "Обновить движок"
                    variant: "outlined"
                    enabled: !page.running
                    onClicked: Backend.startUpdate("engine")
                    KeyNavigation.left: strategiesButton
                }

                Item { Layout.fillWidth: true }
            }
        }

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
                        color: Theme.colors.on_surface
                        font.family: "monospace"
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }
                }

                Text {
                    visible: page.lines.length === 0
                    text: "Журнал обновления появится здесь."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                }
            }
        }
    }
}
