import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран WARP (usque): движок, аккаунт, настройки туннеля, диагностика.
Item {
    id: page

    signal back()

    readonly property var details: Backend.warpDetails
    readonly property var state: details.state !== undefined ? details.state : ({})
    readonly property var lists: details.lists !== undefined ? details.lists : []

    //: Форма настроек; инициализируется из utils/warp.txt при загрузке страницы.
    property var form: ({
        mode: "full", transport: "auto", sni: "", mtu: "1280", dns: "link",
        help_tunnel: true, lists: [], exclude: [], uids: ""
    })
    property string journalText: ""
    property string traceText: ""
    readonly property bool busy: Backend.warpBusy

    function reloadForm() {
        const s = page.state
        page.form = {
            mode: s.mode || "full",
            transport: s.transport || "auto",
            sni: s.sni || (page.details.default_sni || ""),
            mtu: s.mtu || "1280",
            dns: s.dns || "link",
            help_tunnel: String(s.help_tunnel === undefined ? "1" : s.help_tunnel) === "1",
            lists: String(s.lists || "").split(",").filter(function (item) { return item.length > 0 }),
            exclude: String(s.exclude || "").split(",").filter(function (item) { return item.length > 0 }),
            uids: s.uids || ""
        }
    }

    function setField(name, value) {
        const next = Object.assign({}, page.form)
        next[name] = value
        page.form = next
    }

    function toggleInList(name, item) {
        const items = (page.form[name] || []).slice()
        const index = items.indexOf(item)
        if (index >= 0) {
            items.splice(index, 1)
        } else {
            items.push(item)
        }
        page.setField(name, items)
    }

    function saveSettings() {
        const payload = {
            mode: page.form.mode,
            transport: page.form.transport,
            sni: page.form.sni,
            mtu: page.form.mtu,
            dns: page.form.dns,
            help_tunnel: page.form.help_tunnel ? "1" : "0",
            lists: (page.form.lists || []).join(","),
            exclude: (page.form.exclude || []).join(","),
            uids: page.form.uids
        }
        Backend.saveWarpSettings(payload)
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshWarpDetails()
        Backend.refreshWarp()
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

            // --- Шапка -----------------------------------------------------
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
                    KeyNavigation.down: engineInstall
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "WARP (usque)"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        text: Backend.warpInfo.title || "Туннель Cloudflare рядом с zapret"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                BusyIndicator {
                    running: page.busy
                    visible: page.busy
                    implicitWidth: 22
                    implicitHeight: 22
                }
            }

            // --- Движок ----------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Движок usque"
                subtitle: "Клиент MASQUE/Connect-IP из GitHub-релизов"

                Text {
                    width: parent.width
                    text: "Установленная версия: "
                        + (page.details.installed_version
                           ? page.details.installed_version
                           : (page.details.installed ? "неизвестна" : "не установлен"))
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("body_medium")
                }

                Text {
                    width: parent.width
                    text: page.details.upstream_version !== undefined
                        && page.details.upstream_version.length > 0
                        ? "Последняя версия: " + page.details.upstream_version
                        : (page.details.upstream_error !== undefined
                           && page.details.upstream_error.length > 0
                           ? "Не удалось узнать версию: " + page.details.upstream_error
                           : "Последняя версия: не проверялась")
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                Row {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: engineInstall
                        text: page.details.installed ? "Переустановить движок" : "Установить движок"
                        variant: "filled"
                        enabled: !page.busy
                        onClicked: Backend.installWarpEngine()
                        KeyNavigation.down: engineUpdate
                    }

                    MButton {
                        id: engineUpdate
                        text: "Обновить движок"
                        variant: "tonal"
                        enabled: !page.busy && page.details.installed === true
                        onClicked: Backend.updateWarpEngine()
                        KeyNavigation.up: engineInstall
                        KeyNavigation.right: engineCheck
                    }

                    MButton {
                        id: engineCheck
                        text: "Проверить обновления"
                        variant: "outlined"
                        enabled: !page.busy
                        onClicked: Backend.refreshWarpUpstream()
                        KeyNavigation.left: engineUpdate
                        KeyNavigation.down: tosCheck
                    }
                }
            }

            // --- Аккаунт ---------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Аккаунт Cloudflare WARP"
                subtitle: "Регистрация только после согласия с условиями Cloudflare"
                outlined: false

                Text {
                    width: parent.width
                    text: page.details.account ? "Аккаунт зарегистрирован"
                        : "Аккаунт не зарегистрирован"
                    color: page.details.account ? Theme.colors.success : Theme.colors.warning
                    font.pixelSize: Theme.typeSize("body_medium")
                }

                MCheckBox {
                    id: tosCheck
                    text: "Я принимаю условия Cloudflare WARP"
                    font.pixelSize: Theme.typeSize("body_medium")
                }

                MButton {
                    text: "Открыть условия Cloudflare WARP"
                    variant: "text"
                    iconSource: "../icons/next.svg"
                    onClicked: Backend.openWarpTos()
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: registerButton
                        text: page.busy ? "Регистрация…" : "Зарегистрировать аккаунт"
                        variant: "filled"
                        enabled: tosCheck.checked && !page.busy
                        onClicked: Backend.registerWarpAccount(true)
                    }

                    MButton {
                        text: "Удалить аккаунт"
                        variant: "outlined"
                        enabled: page.details.account === true && !page.busy
                        onClicked: Backend.deleteWarpAccount()
                    }
                }
            }

            // --- Настройки туннеля ----------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Настройки туннеля"
                subtitle: "Сохраняются в utils/warp.txt"

                Column {
                    width: parent.width
                    spacing: Theme.spaces.xs

                    MRadioButton {
                        text: "Полный туннель — весь IPv4-трафик идёт через WARP"
                        checked: page.form.mode !== "select"
                        font.pixelSize: Theme.typeSize("body_medium")
                        onClicked: page.setField("mode", "full")
                    }

                    MRadioButton {
                        text: "Только выбранные списки — в туннель уходят указанные диапазоны"
                        checked: page.form.mode === "select"
                        font.pixelSize: Theme.typeSize("body_medium")
                        onClicked: page.setField("mode", "select")
                    }
                }

                // Списки диапазонов — только для селективного режима.
                Column {
                    width: parent.width
                    visible: page.form.mode === "select"
                    spacing: Theme.spaces.xs

                    Text {
                        text: "Списки диапазонов (files/lists/*.txt):"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                    }

                    Repeater {
                        model: page.lists

                        delegate: MCheckBox {
                            text: modelData
                            checked: (page.form.lists || []).indexOf(modelData) >= 0
                            font.pixelSize: Theme.typeSize("body_medium")
                            onClicked: page.toggleInList("lists", modelData)
                        }
                    }

                    Text {
                        text: "Исключения (эти диапазоны всегда идут напрямую):"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                    }

                    Repeater {
                        model: page.lists

                        delegate: MCheckBox {
                            text: modelData
                            checked: (page.form.exclude || []).indexOf(modelData) >= 0
                            font.pixelSize: Theme.typeSize("body_medium")
                            onClicked: page.toggleInList("exclude", modelData)
                        }
                    }

                    TextField {
                        id: uidsField
                        width: parent.width
                        text: page.form.uids
                        placeholderText: "Пользователи (uidrange, например 1000-1000)"
                        selectByMouse: true
                        color: Theme.colors.on_surface
                        placeholderTextColor: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                        onTextEdited: page.setField("uids", text)
                        background: Rectangle {
                            radius: Theme.radii.sm
                            color: Theme.colors.surface_container_high
                            border.width: 1
                            border.color: uidsField.activeFocus ? Theme.colors.primary
                                : Theme.colors.outline_variant
                        }
                    }
                }

                // Транспорт.
                Text {
                    width: parent.width
                    text: "Транспорт"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                ComboBox {
                    id: transportBox
                    width: parent.width
                    model: page.details.transports !== undefined ? page.details.transports : []
                    textRole: "label"
                    currentIndex: {
                        const items = page.details.transports || []
                        for (let i = 0; i < items.length; i++) {
                            if (items[i].value === page.form.transport) {
                                return i
                            }
                        }
                        return 0
                    }
                    onActivated: page.setField("transport", model[currentIndex].value)
                    indicator: Text {
                        x: transportBox.width - width - Theme.spaces.md
                        y: (transportBox.height - height) / 2
                        text: "▾"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: 14
                    }
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: transportBox.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                    contentItem: Text {
                        leftPadding: Theme.spaces.sm
                        verticalAlignment: Text.AlignVCenter
                        text: transportBox.displayText
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        elide: Text.ElideRight
                    }
                }

                // SNI.
                Text {
                    width: parent.width
                    text: "SNI (MASQUE-сервер)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: sniField
                    width: parent.width
                    text: page.form.sni
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("sni", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: sniField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                // MTU.
                Text {
                    width: parent.width
                    text: "MTU (576–1500)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: mtuField
                    width: parent.width
                    text: page.form.mtu
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("mtu", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: mtuField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                // DNS туннеля.
                Text {
                    width: parent.width
                    text: "DNS туннеля"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                ComboBox {
                    id: dnsBox
                    width: parent.width
                    model: page.details.dns_choices !== undefined ? page.details.dns_choices : []
                    textRole: "label"
                    currentIndex: {
                        const items = page.details.dns_choices || []
                        for (let i = 0; i < items.length; i++) {
                            if (items[i].value === page.form.dns) {
                                return i
                            }
                        }
                        return 0
                    }
                    onActivated: page.setField("dns", model[currentIndex].value)
                    indicator: Text {
                        x: dnsBox.width - width - Theme.spaces.md
                        y: (dnsBox.height - height) / 2
                        text: "▾"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: 14
                    }
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: dnsBox.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                    contentItem: Text {
                        leftPadding: Theme.spaces.sm
                        verticalAlignment: Text.AlignVCenter
                        text: dnsBox.displayText
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        elide: Text.ElideRight
                    }
                }

                MSwitch {
                    id: helpSwitch
                    text: "Лечить внешний трафик туннеля через zapret"
                    checked: page.form.help_tunnel === true
                    font.pixelSize: Theme.typeSize("body_medium")
                    onClicked: page.setField("help_tunnel", checked)
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: saveButton
                        text: page.busy ? "Сохранение…" : "Сохранить настройки"
                        variant: "filled"
                        enabled: !page.busy
                        onClicked: page.saveSettings()
                    }
                }
            }

            // --- Диагностика ----------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Диагностика"
                subtitle: "Состояние туннеля и журнал службы"

                Text {
                    width: parent.width
                    text: Backend.warpInfo.detail || ""
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                Text {
                    width: parent.width
                    text: page.traceText.length > 0 ? page.traceText : "Внешний адрес: не проверялся"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        text: "Проверить внешний адрес"
                        variant: "tonal"
                        enabled: !page.busy
                        onClicked: Backend.checkWarpExternalIp()
                    }

                    MButton {
                        text: "Обновить диагностику"
                        variant: "outlined"
                        enabled: !page.busy
                        onClicked: {
                            Backend.refreshWarpDetails()
                            Backend.refreshWarp()
                        }
                    }

                    MButton {
                        text: "Показать журнал"
                        variant: "outlined"
                        onClicked: Backend.loadWarpJournal()
                    }
                }

                TextArea {
                    id: journalArea
                    width: parent.width
                    height: 160
                    readOnly: true
                    wrapMode: TextArea.WrapAnywhere
                    text: page.journalText.length > 0 ? page.journalText
                        : "Журнал не загружен"
                    color: Theme.colors.on_surface_variant
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_small")
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_lowest
                        border.width: 1
                        border.color: Theme.colors.outline_variant
                    }
                }
            }

            // --- Обслуживание ---------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Обслуживание"
                subtitle: "Если состояние системы разошлось с состоянием приложения"

                Text {
                    width: parent.width
                    text: "Аварийная очистка снимает маршруты, правила, DNS и оставшиеся "
                        + "интерфейсы WARP, даже если файл правил потерян, и выключает WARP."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        text: "Аварийная очистка"
                        variant: "outlined"
                        enabled: !page.busy
                        onClicked: cleanupDialog.open()
                    }

                    MButton {
                        text: "Удалить подсистему WARP"
                        variant: "outlined"
                        enabled: !page.busy
                        onClicked: uninstallDialog.open()
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    // --- Подтверждения ------------------------------------------------------

    Dialog {
        id: cleanupDialog

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
                text: "Снять состояние WARP?"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: "Служба будет остановлена, маршруты, правила и DNS сняты. WARP будет "
                    + "выключен в приложении."
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
                    onClicked: cleanupDialog.close()
                }

                MButton {
                    text: "Очистить"
                    variant: "filled"
                    onClicked: {
                        cleanupDialog.close()
                        Backend.warpEmergencyCleanup()
                    }
                }
            }
        }
    }

    Dialog {
        id: uninstallDialog

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
                text: "Удалить подсистему WARP?"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: "Будут удалены служба warp.service, скрипты и движок в /opt/warp, "
                    + "аккаунт Cloudflare и настройки WARP. После установки заново "
                    + "потребуется регистрация."
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
                    onClicked: uninstallDialog.close()
                }

                MButton {
                    text: "Удалить"
                    variant: "filled"
                    onClicked: {
                        uninstallDialog.close()
                        Backend.uninstallWarp()
                    }
                }
            }
        }
    }

    Connections {
        target: Backend

        function onWarpDetailsChanged() {
            page.reloadForm()
        }

        function onWarpJournalChanged() {
            page.journalText = Backend.warpJournal
        }

        function onWarpTraceChanged() {
            page.traceText = Backend.warpTrace
        }
    }
}
