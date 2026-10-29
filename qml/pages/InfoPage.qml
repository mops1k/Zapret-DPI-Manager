import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Информация о приложении: версия, ссылки, каталоги.
Item {
    id: page

    signal back()

    readonly property var info: Backend.info

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshInfo()
        focusTimer.start()
    }

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
                KeyNavigation.down: githubButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Информация"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Zapret DPI Manager · версия " + (page.info.version || "—")
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "О приложении"
            subtitle: "Zapret DPI Manager для Steam Deck и SteamOS"

            Text {
                width: parent.width
                text: "Менеджер службы zapret: обход DPI для YouTube, Discord и Telegram, "
                    + "игровые пресеты, WARP и работа с hostlist/ipset."
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                text: "Каталог данных: " + (page.info.managerDir || "—")
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                width: parent.width
                spacing: Theme.spaces.sm

                MButton {
                    id: githubButton
                    text: "Открыть GitHub"
                    variant: "filled"
                    onClicked: Qt.openUrlExternally(page.info.github || "")
                    KeyNavigation.right: guideButton
                }

                MButton {
                    id: guideButton
                    text: "Руководство"
                    variant: "tonal"
                    onClicked: Backend.openUserGuide()
                    KeyNavigation.left: githubButton
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "Благодарности"
            subtitle: "Проекты, на которых построена работа"

            Text {
                width: parent.width
                text: "Движок zapret — bol-van/zapret · стратегии Flowseal · nfqws сборка "
                    + "ImMALWARE/zapret-linux-easy · WARP-клиент usque · иконки Breeze "
                    + "(KDE, LGPL-3.0-or-later)."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }
        }

        Item { Layout.fillHeight: true }
    }
}
