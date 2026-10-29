import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Настройки DNS: активное соединение, серверы, сброс на DHCP.
Item {
    id: page

    signal back()

    readonly property var dns: Backend.dnsState

    property string servers: ""
    property bool loaded: false

    //: Готовые наборы DNS-серверов.
    readonly property var presets: [
        { name: "Cloudflare", value: "1.1.1.1 1.0.0.1" },
        { name: "Google", value: "8.8.8.8 8.8.4.4" },
        { name: "Quad9", value: "9.9.9.9 149.112.112.112" },
        { name: "AdGuard", value: "94.140.14.14 94.140.15.15" }
    ]

    function reload() {
        page.servers = page.dns.raw || ""
        page.loaded = true
    }

    function apply() {
        Backend.applyDns(page.servers)
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshDns()
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
                KeyNavigation.down: serversField
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Настройки DNS"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Статические DNS для активного соединения (NetworkManager + resolvectl)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: "Активное соединение"
            subtitle: "Изменения применяются к нему"

            Text {
                width: parent.width
                text: page.dns.connection
                    ? page.dns.connection + " · " + (page.dns.device || "—")
                    : "Активное подключение не найдено"
                color: page.dns.connection ? Theme.colors.on_surface : Theme.colors.error
                font.pixelSize: Theme.typeSize("body_large")
            }

            Text {
                width: parent.width
                text: "Текущие DNS: " + ((page.dns.servers || []).length > 0
                    ? page.dns.servers.join(", ") : "автоматически (DHCP)")
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                visible: (page.dns.resolv || "").length > 0
                text: "resolvectl: " + page.dns.resolv
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }
        }

        Card {
            Layout.fillWidth: true
            title: "DNS-серверы"
            subtitle: "Через пробел или запятую · нужен пароль sudo"

            TextField {
                id: serversField
                width: parent.width
                placeholderText: "1.1.1.1 8.8.8.8"
                text: page.loaded ? page.servers : ""
                selectByMouse: true
                color: Theme.colors.on_surface
                placeholderTextColor: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_large")
                onTextEdited: page.servers = text
                background: Rectangle {
                    radius: Theme.radii.sm
                    color: Theme.colors.surface_container_high
                    border.width: 1
                    border.color: serversField.activeFocus ? Theme.colors.primary
                        : Theme.colors.outline_variant
                }
                KeyNavigation.down: presetsRow
            }

            RowLayout {
                id: presetsRow
                width: parent.width
                spacing: Theme.spaces.xs

                Repeater {
                    model: page.presets

                    delegate: MButton {
                        text: modelData.name
                        variant: "text"
                        onClicked: {
                            page.servers = modelData.value
                            serversField.forceActiveFocus()
                        }
                        KeyNavigation.down: applyButton
                    }
                }

                Item { Layout.fillWidth: true }
            }

            RowLayout {
                width: parent.width
                spacing: Theme.spaces.sm

                MButton {
                    id: applyButton
                    text: "Применить"
                    variant: "filled"
                    enabled: !!page.dns.connection
                    onClicked: page.apply()
                    KeyNavigation.right: resetButton
                    KeyNavigation.up: serversField
                }

                MButton {
                    id: resetButton
                    text: "Сбросить на авто"
                    variant: "outlined"
                    enabled: !!page.dns.connection
                    onClicked: Backend.resetDns()
                    KeyNavigation.left: applyButton
                    KeyNavigation.right: refreshButton
                }

                MButton {
                    id: refreshButton
                    text: "Обновить"
                    variant: "text"
                    onClicked: Backend.refreshDns()
                    KeyNavigation.left: resetButton
                }

                Item { Layout.fillWidth: true }
            }

            Text {
                width: parent.width
                text: "Соединение переподключается: сеть может пропасть на несколько секунд."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }
        }

        Item { Layout.fillHeight: true }
    }

    Connections {
        target: Backend

        function onDnsChanged() {
            if (!page.loaded) {
                page.reload()
            }
        }
    }
}
