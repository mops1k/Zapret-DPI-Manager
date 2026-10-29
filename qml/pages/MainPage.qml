import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Главный экран: статус службы, текущая стратегия, меню настроек.
Item {
    id: page

    signal openStrategy()
    signal openWarp()
    signal openHostlist()
    signal openIpset()
    signal openConnectionCheck()
    signal openDns()
    signal openGameFilter()
    signal openUpdate()
    signal openUnlock()
    signal openInfo()
    signal openReport()

    //: Флаг: уходили ли с этого экрана (нужно, чтобы при старте фокус
    //: оставался на кнопке службы, а не прыгал на «Сменить»).
    property bool leftOnce: false

    readonly property var menuItems: [
        { key: "strategy", label: "Сменить стратегию", icon: "../icons/strategy.svg" },
        { key: "connection", label: "Проверка соединения", icon: "../icons/connection.svg" },
        { key: "hostlist", label: "Настройки Hostlist", icon: "../icons/hostlist.svg" },
        { key: "ipset", label: "Настройки IPSet", icon: "../icons/ipset.svg" },
        { key: "dns", label: "Настройки DNS", icon: "../icons/dns.svg" },
        { key: "unlock", label: "Разблокировать сервисы", icon: "../icons/unlock.svg" },
        { key: "update", label: "Обновить Zapret", icon: "../icons/update.svg" },
        { key: "forget", label: "Забыть пароль sudo", icon: "../icons/password.svg" },
        { key: "uninstall", label: "Удалить Zapret", icon: "../icons/delete.svg" }
    ]

    //: Позиция меню считается в момент открытия: при создании страницы
    //: раскладки ещё нет, и привязки к mapToItem дают отрицательные координаты.
    function openSettingsMenu() {
        const button = appBar.settingsButton
        const buttonPoint = button.mapToItem(page, 0, 0)
        const barPoint = appBar.mapToItem(page, 0, appBar.height)
        const margin = Theme.spaces.sm
        const maxX = Math.max(margin, page.width - settingsPopup.width - margin)
        const maxY = Math.max(margin, page.height - settingsPopup.height - margin)
        settingsPopup.x = Math.min(Math.max(buttonPoint.x + button.width - settingsPopup.width,
                                             margin), maxX)
        settingsPopup.y = Math.min(barPoint.y + Theme.spaces.sm, maxY)
        settingsPopup.open()
    }

    function toggleSettingsMenu() {
        if (settingsPopup.opened) {
            settingsPopup.close()
        } else {
            openSettingsMenu()
        }
    }

    //: Переключение WARP: при help_tunnel=1 сначала предлагаем включить zapret.
    function requestWarpToggle(enable) {
        if (!enable) {
            Backend.setWarpEnabled(false, false)
            return
        }
        if (Backend.warpNeedsZapret()) {
            warpOfferDialog.open()
            return
        }
        Backend.setWarpEnabled(true, false)
    }

    function handleMenu(key) {
        settingsPopup.close()
        switch (key) {
        case "strategy":
            page.leaveToStrategy()
            return
        case "connection":
            page.openConnectionCheck()
            return
        case "dns":
            page.openDns()
            return
        case "update":
            page.openUpdate()
            return
        case "unlock":
            page.openUnlock()
            return
        case "hostlist":
            page.openHostlist()
            return
        case "ipset":
            page.openIpset()
            return
        case "report":
            page.openReport()
            return
        case "forget":
            Backend.forgetSudoPassword()
            return
        case "uninstall":
            Backend.showMessage("Удаление Zapret появится на следующем этапе порта", "warning")
            return
        default:
            Backend.showMessage("Раздел «" + key + "» появится на следующем этапе порта", "info")
        }
    }

    Component.onCompleted: serviceToggle.forceActiveFocus()

    //: Возврат со вложенного экрана: ставим фокус на «Сменить».
    function restoreFocus() {
        strategyChange.forceActiveFocus()
    }

    function leaveToStrategy() {
        leftOnce = true
        openStrategy()
    }

    function leaveToWarp() {
        leftOnce = true
        openWarp()
    }

    onVisibleChanged: if (visible && leftOnce) Qt.callLater(restoreFocus)

    Flickable {
        id: flick
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: contentColumn.implicitHeight + Theme.spaces.lg * 2
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: contentColumn

            x: Math.max(Theme.spaces.lg, (flick.width - width) / 2)
            y: Theme.spaces.lg
            width: Math.min(flick.width - 2 * Theme.spaces.xl, 900)
            spacing: Theme.spaces.md

            AppBar {
                id: appBar
                Layout.fillWidth: true
                serviceState: Backend.serviceState
                active: Backend.serviceActive
                stateLabel: Backend.serviceStateLabel
                busy: Backend.busy
                navDown: serviceToggle
                onRestartClicked: Backend.restartService()
                onGameFilterClicked: page.openGameFilter()
                onSettingsClicked: page.toggleSettingsMenu()
                onGuideClicked: Backend.openUserGuide()
                onInfoClicked: page.openInfo()
            }

            // Карточка службы.
            Card {
                Layout.fillWidth: true
                title: "Служба Zapret DPI"
                subtitle: "Движок nfqws и правила NFQUEUE"

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Rectangle {
                        implicitWidth: 10
                        implicitHeight: 10
                        radius: width / 2
                        Layout.alignment: Qt.AlignVCenter
                        color: Backend.serviceActive ? Theme.colors.success
                            : Backend.serviceState === "failed" ? Theme.colors.error
                            : Backend.serviceState === "activating"
                                || Backend.serviceState === "deactivating" ? Theme.colors.warning
                            : Theme.colors.outline
                    }

                    Text {
                        Layout.fillWidth: true
                        text: Backend.serviceExists
                            ? Backend.serviceStateLabel
                            : "Служба zapret.service не установлена"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        elide: Text.ElideRight
                    }

                    BusyIndicator {
                        running: Backend.busy
                        visible: Backend.busy
                        implicitWidth: 22
                        implicitHeight: 22
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: serviceToggle
                        Layout.fillWidth: true
                        text: Backend.serviceActive
                            ? "Остановить Zapret DPI"
                            : "Запустить Zapret DPI"
                        variant: Backend.serviceActive ? "tonal" : "filled"
                        enabled: !Backend.busy
                        onClicked: Backend.toggleService()
                        focus: true
                        KeyNavigation.up: appBar.settingsButton
                        KeyNavigation.right: autostartToggle
                        KeyNavigation.down: warpToggle
                    }

                    MButton {
                        id: autostartToggle
                        Layout.fillWidth: true
                        variant: "outlined"
                        text: Backend.autostartEnabled
                            ? "Отключить автозапуск"
                            : "Включить автозапуск"
                        enabled: !Backend.busy
                        onClicked: Backend.toggleAutostart()
                        KeyNavigation.left: serviceToggle
                        KeyNavigation.up: appBar.restartButton
                        KeyNavigation.down: warpSettings
                    }
                }
            }

            // Карточка WARP (usque).
            Card {
                Layout.fillWidth: true
                title: "WARP (Cloudflare)"
                subtitle: "Туннель usque рядом с zapret"

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Rectangle {
                        implicitWidth: 10
                        implicitHeight: 10
                        radius: width / 2
                        Layout.alignment: Qt.AlignVCenter
                        color: {
                            const kind = Backend.warpInfo.kind
                            if (kind === "success")
                                return Theme.colors.success
                            if (kind === "error")
                                return Theme.colors.error
                            if (kind === "warning")
                                return Theme.colors.warning
                            return Theme.colors.outline
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: Backend.warpInfo.title || "Проверка WARP…"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        elide: Text.ElideRight
                    }

                    BusyIndicator {
                        running: Backend.warpBusy
                        visible: Backend.warpBusy
                        implicitWidth: 22
                        implicitHeight: 22
                    }
                }

                Text {
                    width: parent.width
                    visible: text.length > 0
                    text: Backend.warpInfo.detail || ""
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: warpToggle
                        Layout.fillWidth: true
                        variant: Backend.warpInfo.active ? "tonal" : "filled"
                        text: Backend.warpInfo.active ? "Выключить WARP" : "Включить WARP"
                        enabled: Backend.warpInfo.installed === true && !Backend.warpBusy
                        onClicked: page.requestWarpToggle(Backend.warpInfo.active !== true)
                        KeyNavigation.up: autostartToggle
                        KeyNavigation.down: strategyChange
                    }

                    MButton {
                        id: warpSettings
                        variant: "outlined"
                        text: "Настроить"
                        onClicked: page.openWarp()
                        KeyNavigation.left: warpToggle
                        KeyNavigation.up: autostartToggle
                        KeyNavigation.down: strategyChange
                    }
                }
            }

            // Карточка стратегии.
            Card {
                Layout.fillWidth: true
                title: "Текущая стратегия"
                subtitle: "Набор аргументов nfqws из config.txt"
                outlined: false

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Text {
                        text: "Стратегия:"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                    }

                    Text {
                        Layout.fillWidth: true
                        text: Backend.strategyName
                        color: Theme.colors.primary
                        font.pixelSize: Theme.typeSize("title_medium")
                        elide: Text.ElideRight
                    }

                    MButton {
                        id: strategyChange
                        text: "Сменить"
                        variant: "outlined"
                        iconSource: "../icons/next.svg"
                        onClicked: page.leaveToStrategy()
                        KeyNavigation.up: warpToggle
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    // --- Диалог: включить zapret перед WARP ----------------------------------

    Dialog {
        id: warpOfferDialog

        anchors.centerIn: parent
        width: Math.min(page.width - 2 * Theme.spaces.xl, 520)
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
                text: "Включить zapret вместе с WARP?"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: "«Лечение внешнего трафика туннеля» работает только при активном zapret: "
                    + "иначе WARP может подключиться, но данные через туннель не пойдут "
                    + "и маршруты будут сняты автоматически."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Item { Layout.fillWidth: true }

                MButton {
                    text: "Только WARP"
                    variant: "text"
                    onClicked: {
                        warpOfferDialog.close()
                        Backend.setWarpEnabled(true, false)
                    }
                }

                MButton {
                    text: "Включить zapret"
                    variant: "filled"
                    onClicked: {
                        warpOfferDialog.close()
                        Backend.setWarpEnabled(true, true)
                    }
                }
            }
        }
    }

    // --- Меню настроек ------------------------------------------------------

    Popup {
        id: settingsPopup

        objectName: "settingsPopup"
        parent: page
        x: 0
        y: 0
        width: 300
        padding: 0
        modal: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: menuRepeater.count > 0 ? menuRepeater.itemAt(0).forceActiveFocus() : null
        onClosed: appBar.settingsButton.forceActiveFocus()

        background: Rectangle {
            color: Theme.colors.surface_container
            radius: Theme.radii.md
            border.width: 1
            border.color: Theme.colors.outline_variant
        }

        contentItem: Column {
            x: Theme.spaces.sm
            y: Theme.spaces.sm
            width: settingsPopup.width - Theme.spaces.sm * 2

            Repeater {
                id: menuRepeater
                model: page.menuItems

                delegate: MButton {
                    width: parent.width
                    variant: "text"
                    text: modelData.label
                    iconSource: modelData.icon
                    onClicked: page.handleMenu(modelData.key)
                    KeyNavigation.up: index > 0
                        ? menuRepeater.itemAt(index - 1)
                        : null
                    KeyNavigation.down: index < menuRepeater.count - 1
                        ? menuRepeater.itemAt(index + 1)
                        : themeToggle
                }
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.colors.outline_variant
            }

            MButton {
                id: themeToggle
                width: parent.width
                variant: "text"
                iconSource: "../icons/theme.svg"
                text: Theme.dark ? "Светлая тема" : "Тёмная тема"
                onClicked: Theme.toggleMode()
                KeyNavigation.up: menuRepeater.count > 0
                    ? menuRepeater.itemAt(menuRepeater.count - 1)
                    : null
            }
        }
    }
}
