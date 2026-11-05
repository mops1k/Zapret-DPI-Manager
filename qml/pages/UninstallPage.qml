import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Удаление Zapret: предупреждение, подтверждение и журнал удаления.
Item {
    id: page

    objectName: "uninstallPage"

    signal back()

    readonly property bool running: Backend.uninstallRunning
    readonly property bool done: Backend.uninstallDone
    readonly property bool succeeded: Backend.uninstallSucceeded
    readonly property string resultMessage: Backend.uninstallMessage
    readonly property var lines: Backend.uninstallLog

    //: Сколько держать итоговый экран до автоматического закрытия программы.
    readonly property int closeDelayMs: 8000

    Keys.onEscapePressed: if (!page.running && !page.succeeded) page.back()

    Component.onCompleted: focusTimer.start()

    onVisibleChanged: if (visible) focusTimer.start()

    onSucceededChanged: if (page.succeeded) quitTimer.start()

    Timer {
        id: focusTimer
        interval: 60
        onTriggered: uninstallButton.forceActiveFocus()
    }

    //: После удаления файлов менеджера нет — программу пора закрыть.
    Timer {
        id: quitTimer
        interval: page.closeDelayMs
        onTriggered: Backend.quit()
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
                enabled: !page.running && !page.succeeded
                onClicked: page.back()
                ToolTip.visible: hovered
                ToolTip.text: "Назад"
                KeyNavigation.down: uninstallButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Удаление Zapret"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Полное удаление службы, менеджера и подсистем"
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
            visible: !page.done
            title: "Будет удалено"
            subtitle: "Действие необратимо — восстановить настройки не получится"

            Text {
                width: parent.width
                text: "• служба zapret.service и движок nfqws (каталог /opt/zapret)\n"
                    + "• каталог менеджера ~/Zapret_DPI_Manager: стратегии, списки, настройки\n"
                    + "• подсистема WARP и прокси Telegram (TG WS Proxy)\n"
                    + "• ярлык в меню приложений и сохранённый пароль sudo\n"
                    + "• пакеты-зависимости, которые поставил менеджер"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: !page.done
            implicitHeight: warningColumn.implicitHeight + Theme.spaces.md * 2
            radius: Theme.radii.md
            color: Theme.colors.warning_container
            border.width: 1
            border.color: Theme.colors.outline_variant

            Column {
                id: warningColumn

                x: Theme.spaces.md
                y: Theme.spaces.md
                width: parent.width - Theme.spaces.md * 2

                Text {
                    width: warningColumn.width
                    text: "Понадобится пароль администратора — его спросит системное окно sudo. "
                        + "На SteamOS файловая система на время удаления разблокируется "
                        + "и затем блокируется обратно. После удаления программа закроется сама."
                    color: Theme.colors.on_warning_container
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: page.done
            title: page.succeeded ? "Zapret удалён" : "Удаление не завершено"

            Text {
                width: parent.width
                text: page.resultMessage
                color: page.succeeded ? Theme.colors.on_surface : Theme.colors.error
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                visible: page.succeeded
                text: "Файлы менеджера удалены, поэтому программа закроется автоматически. "
                    + "Установить Zapret заново можно установщиком из релиза."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                id: uninstallButton
                text: page.running ? "Удаление…" : "Удалить Zapret"
                variant: "filled"
                //: После ошибки удаление можно повторить, после успеха — уже нет.
                enabled: !page.running && !page.succeeded
                onClicked: confirmDialog.open()
                KeyNavigation.up: backButton
                KeyNavigation.down: closeButton
            }

            MButton {
                id: closeButton
                visible: page.done
                text: page.succeeded ? "Закрыть сейчас" : "Назад"
                variant: "tonal"
                onClicked: {
                    if (page.succeeded) {
                        Backend.quit()
                    } else {
                        page.back()
                    }
                }
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
                        text: "Журнал удаления появится здесь: этапы, проценты и причины ошибок."
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }

    Dialog {
        id: confirmDialog

        anchors.centerIn: parent
        width: Math.min(page.width - Theme.spaces.xxl * 2, 620)
        modal: true

        background: Rectangle {
            color: Theme.colors.surface_container
            radius: Theme.radii.lg
            border.width: 1
            border.color: Theme.colors.outline_variant
        }

        contentItem: ColumnLayout {
            spacing: Theme.spaces.md

            Text {
                Layout.fillWidth: true
                text: "Удалить Zapret?"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: "Будут удалены служба zapret.service, каталог /opt/zapret, "
                    + "менеджер с настройками и стратегиями, подсистема WARP и прокси "
                    + "Telegram. Это действие нельзя отменить."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Item { Layout.fillWidth: true }

                MButton {
                    text: "Отмена"
                    variant: "text"
                    onClicked: confirmDialog.close()
                }

                MButton {
                    text: "Удалить"
                    variant: "filled"
                    onClicked: {
                        confirmDialog.close()
                        Backend.startUninstall()
                    }
                }
            }
        }
    }
}
