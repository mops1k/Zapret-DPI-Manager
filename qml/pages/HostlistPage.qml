import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран Hostlist: сервисы, свои домены, обход Telegram.
Item {
    id: page

    signal back()

    readonly property var hostlist: Backend.hostlistState
    readonly property var serviceNames: hostlist.serviceNames !== undefined
        ? hostlist.serviceNames : []

    property var form: ({
        blocked: "", unblocked: "", services: ({}), telegramRules: false
    })
    property bool loaded: false

    function reload() {
        const data = page.hostlist
        page.form = {
            blocked: data.blocked || "",
            unblocked: data.unblocked || "",
            services: Object.assign({}, data.services || {}),
            telegramRules: data.telegramRules === true
        }
        page.loaded = true
    }

    function toggleService(name) {
        const next = Object.assign({}, page.form)
        const services = Object.assign({}, next.services)
        services[name] = !services[name]
        next.services = services
        page.form = next
    }

    function save() {
        Backend.saveHostlist({
            blocked: page.form.blocked,
            unblocked: page.form.unblocked,
            services: page.form.services,
            telegramRules: page.form.telegramRules
        })
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshHostlist()
        focusTimer.start()
    }

    onVisibleChanged: if (visible) focusTimer.start()

    Timer {
        id: focusTimer
        interval: 60
        onTriggered: backButton.forceActiveFocus()
    }

    Flickable {
        id: flick
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: column.implicitHeight + Theme.spaces.lg * 2
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: column

            x: Math.max(Theme.spaces.lg, (flick.width - width) / 2)
            y: Theme.spaces.lg
            width: Math.min(flick.width - 2 * Theme.spaces.xl, 900)
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
                    KeyNavigation.down: serviceBox
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "Настройки Hostlist"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    Text {
                        text: "Свои домены пишутся в files/lists/*_user.txt"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
            }

            // --- Сервисы ---------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Сервисы"
                subtitle: "Домены добавляются в list-general_user.txt"

                Column {
                    id: serviceBox
                    width: parent.width
                    spacing: Theme.spaces.xs

                    Repeater {
                        model: page.serviceNames

                        delegate: MCheckBox {
                            text: modelData
                            checked: page.form.services[modelData] === true
                            onClicked: page.toggleService(modelData)
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: 1
                        color: Theme.colors.outline_variant
                    }

                    MCheckBox {
                        text: "Обход Telegram (TCP + звонки)"
                        checked: page.form.telegramRules === true
                        onClicked: {
                            const next = Object.assign({}, page.form)
                            next.telegramRules = !next.telegramRules
                            page.form = next
                        }
                    }

                    Text {
                        width: parent.width
                        text: "Добавляет в config.txt три правила обхода Telegram: "
                            + "домены, IP-диапазоны MTProto и UDP-звонки."
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // --- Заблокированные домены -----------------------------------
            Card {
                Layout.fillWidth: true
                title: "Заблокированные домены"
                subtitle: "По одному домену на строку · сохраняются в list-general_user.txt"

                TextArea {
                    id: blockedArea
                    width: parent.width
                    height: 180
                    wrapMode: TextArea.WrapAnywhere
                    text: page.loaded ? page.form.blocked : ""
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_small")
                    onTextChanged: {
                        if (page.loaded && page.form.blocked !== text) {
                            const next = Object.assign({}, page.form)
                            next.blocked = text
                            page.form = next
                        }
                    }
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: blockedArea.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                Text {
                    width: parent.width
                    text: "Строки с # считаются комментариями и не проверяются."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }
            }

            // --- Незаблокированные домены ---------------------------------
            Card {
                Layout.fillWidth: true
                title: "Незаблокированные домены"
                subtitle: "Исключения · сохраняются в list-exclude_user.txt"

                TextArea {
                    id: unblockedArea
                    width: parent.width
                    height: 140
                    wrapMode: TextArea.WrapAnywhere
                    text: page.loaded ? page.form.unblocked : ""
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_small")
                    onTextChanged: {
                        if (page.loaded && page.form.unblocked !== text) {
                            const next = Object.assign({}, page.form)
                            next.unblocked = text
                            page.form = next
                        }
                    }
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: unblockedArea.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                MButton {
                    id: saveButton
                    text: "Сохранить"
                    variant: "filled"
                    iconSource: "../icons/next.svg"
                    onClicked: page.save()
                    KeyNavigation.up: blockedArea
                }

                Text {
                    Layout.fillWidth: true
                    text: "Домены проверяются; при ошибке ничего не сохраняется."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    elide: Text.ElideRight
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    Connections {
        target: Backend

        function onHostlistChanged() {
            if (!page.loaded) {
                page.reload()
            }
        }
    }
}
