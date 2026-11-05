import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Проверка системы: зависимости, установка zapret, целостность файлов менеджера.
Item {
    id: page

    objectName: "systemCheckPage"

    signal back()

    readonly property var state: Backend.systemCheckState
    readonly property bool running: Backend.systemCheckRunning
    readonly property bool repairing: Backend.systemCheckRepairing
    readonly property bool busy: page.running || page.repairing
    readonly property bool checked: page.state.checked === true
    readonly property bool healthy: page.checked && page.state.ok === true
    readonly property var problems: page.state.problems !== undefined
        ? page.state.problems : []
    readonly property var missingDependencies: page.state.missingDependencies !== undefined
        ? page.state.missingDependencies : []
    readonly property int missingFiles: page.state.missingFiles !== undefined
        ? page.state.missingFiles : 0
    readonly property int missingDirs: page.state.missingDirs !== undefined
        ? page.state.missingDirs : 0
    readonly property bool zapretInstalled: page.state.zapretInstalled === true
    readonly property var lines: Backend.systemCheckLog

    Keys.onEscapePressed: if (!page.busy) page.back()

    Component.onCompleted: {
        if (!page.checked) {
            Backend.checkSystem(false)
        }
        focusTimer.start()
    }

    onVisibleChanged: if (visible) focusTimer.start()

    Timer {
        id: focusTimer
        interval: 60
        onTriggered: checkButton.forceActiveFocus()
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
                enabled: !page.busy
                onClicked: page.back()
                ToolTip.visible: hovered
                ToolTip.text: "Назад"
                KeyNavigation.down: checkButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Проверка системы"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: page.repairing ? "Исправление…"
                        : page.running ? "Проверка…"
                        : page.state.checkedAt !== undefined && page.state.checkedAt.length > 0
                            ? "Последняя проверка: " + page.state.checkedAt
                            : "Зависимости, установка Zapret и файлы менеджера"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }

            BusyIndicator {
                running: page.busy
                visible: page.busy
                implicitWidth: 22
                implicitHeight: 22
            }
        }

        Card {
            Layout.fillWidth: true
            title: page.healthy ? "Система в порядке"
                : page.checked ? "Найдены проблемы" : "Состояние"
            subtitle: !page.checked ? "Проверка выполняется при открытии экрана"
                : page.healthy ? "Всё на месте"
                : "Проблем: " + page.problems.length

            Text {
                width: parent.width
                text: "Зависимости: " + (page.missingDependencies.length > 0
                        ? "не хватает " + page.missingDependencies.join(", ")
                        : "все на месте")
                color: page.missingDependencies.length > 0
                    ? Theme.colors.error : Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                text: "Zapret: " + (page.zapretInstalled
                        ? "установлен"
                        : "не установлен (нет /opt/zapret или службы zapret.service)")
                color: page.zapretInstalled ? Theme.colors.on_surface : Theme.colors.error
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                visible: page.zapretInstalled
                text: "Файлы менеджера: " + ((page.missingFiles + page.missingDirs) > 0
                        ? "повреждены — отсутствуют файлы (" + page.missingFiles
                            + ") и каталоги (" + page.missingDirs + ")"
                        : "в порядке")
                color: (page.missingFiles + page.missingDirs) > 0
                    ? Theme.colors.error : Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                text: "Проверка только читает систему. Установка зависимостей, восстановление "
                    + "файлов и установка Zapret запускаются кнопкой «Исправить» и требуют "
                    + "пароль администратора."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                id: checkButton
                text: page.running ? "Проверка…" : "Проверить снова"
                variant: "tonal"
                enabled: !page.busy
                onClicked: Backend.checkSystem(false)
                KeyNavigation.up: backButton
                KeyNavigation.right: repairButton
            }

            MButton {
                id: repairButton
                text: page.repairing ? "Исправление…" : "Исправить"
                variant: "filled"
                visible: page.checked && !page.healthy
                enabled: !page.busy
                onClicked: Backend.repairSystem()
                KeyNavigation.left: checkButton
            }

            Item { Layout.fillWidth: true }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radii.md
            color: Theme.colors.surface_container_low
            border.width: 1
            border.color: Theme.colors.outline_variant

            Flickable {
                id: logFlick
                anchors.fill: parent
                anchors.margins: Theme.spaces.md
                clip: true
                contentWidth: width
                contentHeight: logColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                onContentHeightChanged: contentY = Math.max(0, contentHeight - height)

                Column {
                    id: logColumn

                    width: logFlick.width
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
                        text: "Журнал проверки появится здесь."
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
}
