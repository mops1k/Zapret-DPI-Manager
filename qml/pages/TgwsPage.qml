import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран TG WS Proxy: служба, ссылка для Telegram, параметры, журнал.
Item {
    id: page

    signal back()

    readonly property var info: Backend.tgwsInfo
    readonly property bool busy: Backend.tgwsBusy
    readonly property string link: info.link || ""

    //: Форма настроек; перечитывается из utils/tgws.txt при показе страницы.
    property var form: ({ port: "1443", secret: "", cf: "", worker: "", fakeTls: "" })
    property string logText: ""

    function reloadForm() {
        page.form = {
            port: String(info.port || "1443"),
            secret: String(info.secret || ""),
            cf: String(info.cf_domains || ""),
            worker: String(info.worker_domains || ""),
            fakeTls: String(info.fake_tls || "")
        }
    }

    function setField(name, value) {
        const next = Object.assign({}, page.form)
        next[name] = value
        page.form = next
    }

    function saveSettings() {
        Backend.saveTgwsSettings(page.form.port, page.form.secret,
                                 page.form.cf, page.form.worker,
                                 page.form.fakeTls)
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        page.reloadForm()
        Backend.refreshTgws()
        Backend.refreshTgwsLog()
        focusTimer.start()
    }

    onVisibleChanged: if (visible) {
        page.reloadForm()
        Backend.refreshTgws()
        focusTimer.start()
    }

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
                    KeyNavigation.down: toggleButton
                }

                Column {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        text: "Прокси Telegram (WS)"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }

                    Text {
                        width: parent.width
                        text: "Локальный MTProto-прокси через WebSocket: "
                            + "работает как отдельная служба и не выключается вместе с приложением"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // --- Состояние -------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Состояние"
                subtitle: "Пользовательская служба systemd (sudo не нужен)"

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Rectangle {
                        implicitWidth: 10
                        implicitHeight: 10
                        radius: width / 2
                        Layout.alignment: Qt.AlignVCenter
                        color: {
                            const kind = page.info.kind
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
                        text: page.info.title || "Проверка прокси…"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        elide: Text.ElideRight
                    }

                    BusyIndicator {
                        running: page.busy
                        visible: page.busy
                        implicitWidth: 22
                        implicitHeight: 22
                    }
                }

                Text {
                    width: parent.width
                    visible: text.length > 0
                    text: page.info.detail || ""
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: toggleButton
                        Layout.fillWidth: true
                        variant: page.info.active ? "tonal" : "filled"
                        text: page.info.active ? "Выключить прокси" : "Включить прокси"
                        enabled: page.info.engine_ready === true && !page.busy
                        onClicked: Backend.setTgwsEnabled(page.info.active !== true)
                        KeyNavigation.down: telegramButton
                    }

                    MButton {
                        id: telegramButton
                        text: "Добавить в Telegram"
                        variant: "outlined"
                        enabled: page.link.length > 0 && !page.busy
                        onClicked: Backend.openTgwsInTelegram()
                        KeyNavigation.left: toggleButton
                        KeyNavigation.down: copyButton
                    }

                    MButton {
                        id: copyButton
                        text: "Скопировать ссылку"
                        variant: "text"
                        enabled: page.link.length > 0
                        onClicked: Backend.copyTgwsLink()
                        KeyNavigation.left: telegramButton
                        KeyNavigation.up: telegramButton
                    }
                }

                Text {
                    width: parent.width
                    visible: page.info.engine_ready !== true
                    text: "Файлы движка tgws не найдены рядом с приложением: "
                        + "установите полный пакет менеджера."
                    color: Theme.colors.error
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }
            }

            // --- Ссылка ----------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Подключение в Telegram"
                subtitle: "Настройки → Продвинутые настройки → Тип подключения → Прокси"

                Text {
                    width: parent.width
                    text: "Кнопка «Добавить в Telegram» открывает ссылку tg://proxy: "
                        + "клиент сам предложит добавить сервер, порт и секрет. "
                        + "Если Telegram не открылся, добавьте вручную:"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                TextArea {
                    id: linkArea
                    width: parent.width
                    height: 64
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextArea.WrapAnywhere
                    text: page.link.length > 0 ? page.link : "Ссылка появится после запуска прокси"
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

                Text {
                    width: parent.width
                    text: "Сервер: " + (page.info.host || "127.0.0.1")
                        + "   Порт: " + (page.info.port || "1443")
                        + "   Секрет: " + (page.info.secret || "—")
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }
            }

            // --- Параметры -------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Параметры"
                subtitle: "Изменения применяются к службе, активный прокси перезапускается"

                Text {
                    text: "Порт прослушивания"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: portField
                    width: parent.width
                    text: page.form.port
                    inputMethodHints: Qt.ImhDigitsOnly
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    placeholderTextColor: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("port", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: portField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                Text {
                    text: "Секрет MTProto (32 hex-символа)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    TextField {
                        id: secretField
                        Layout.fillWidth: true
                        text: page.form.secret
                        selectByMouse: true
                        color: Theme.colors.on_surface
                        placeholderTextColor: Theme.colors.on_surface_variant
                        font.family: "monospace"
                        font.pixelSize: Theme.typeSize("body_medium")
                        onTextEdited: page.setField("secret", text)
                        background: Rectangle {
                            radius: Theme.radii.sm
                            color: Theme.colors.surface_container_high
                            border.width: 1
                            border.color: secretField.activeFocus ? Theme.colors.primary
                                : Theme.colors.outline_variant
                        }
                    }

                    MButton {
                        text: "Новый"
                        variant: "outlined"
                        onClicked: page.setField("secret", Backend.newTgwsSecret())
                    }
                }

                Text {
                    text: "Свои Cloudflare-домены для фолбэка (через запятую)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: cfField
                    width: parent.width
                    text: page.form.cf
                    selectByMouse: true
                    placeholderText: "например, proxy.example.com"
                    color: Theme.colors.on_surface
                    placeholderTextColor: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("cf", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: cfField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                Text {
                    text: "Cloudflare Worker домены (через запятую)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: workerField
                    width: parent.width
                    text: page.form.worker
                    selectByMouse: true
                    placeholderText: "например, worker.example.workers.dev"
                    color: Theme.colors.on_surface
                    placeholderTextColor: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("worker", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: workerField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                Text {
                    text: "Домен маскировки fake-TLS (необязательно)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                }

                TextField {
                    id: fakeTlsField
                    width: parent.width
                    text: page.form.fakeTls
                    selectByMouse: true
                    placeholderText: "например, cloudflare.com"
                    color: Theme.colors.on_surface
                    placeholderTextColor: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    onTextEdited: page.setField("fakeTls", text)
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: fakeTlsField.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                MButton {
                    text: page.busy ? "Сохранение…" : "Сохранить настройки"
                    variant: "filled"
                    enabled: !page.busy
                    onClicked: page.saveSettings()
                }
            }

            // --- Служба ----------------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Служба"
                subtitle: "systemctl --user: работает без пароля и после закрытия окна"

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Text {
                        Layout.fillWidth: true
                        text: page.info.enabled ? "Автозапуск включён" : "Автозапуск выключен"
                        color: page.info.enabled ? Theme.colors.success
                            : Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                    }

                    MButton {
                        text: page.info.enabled ? "Выключить автозапуск" : "Включить автозапуск"
                        variant: "outlined"
                        enabled: page.info.installed === true && !page.busy
                        onClicked: Backend.setTgwsEnabled(page.info.active !== true)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    Text {
                        Layout.fillWidth: true
                        text: page.info.linger
                            ? "Прокси переживает выход из системы (linger включён)"
                            : "Без linger прокси остановится при выходе из системы"
                        color: page.info.linger ? Theme.colors.success
                            : Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }

                    MButton {
                        text: "Включить linger"
                        variant: "text"
                        enabled: !page.info.linger
                        onClicked: Backend.enableTgwsLinger()
                    }
                }

                Text {
                    width: parent.width
                    text: "Журнал: " + (page.info.log_path || "")
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                MButton {
                    text: "Обновить журнал"
                    variant: "outlined"
                    onClicked: Backend.refreshTgwsLog()
                }

                TextArea {
                    id: logArea
                    width: parent.width
                    height: 200
                    readOnly: true
                    wrapMode: TextArea.WrapAnywhere
                    text: page.logText.length > 0 ? page.logText : "Журнал не загружен"
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

            Item { Layout.fillHeight: true }
        }
    }

    Connections {
        target: Backend

        function onTgwsChanged() {
            page.reloadForm()
        }

        function onTgwsLogChanged() {
            page.logText = Backend.tgwsLog
        }
    }
}
