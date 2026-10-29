import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран IPSet: пользовательские диапазоны и режим фильтра.
Item {
    id: page

    signal back()

    readonly property var ipset: Backend.ipsetState
    property var form: ({ blocked: "", unblocked: "", telegram: "" })
    property string filterMode: "none"
    property string tab: "blocked"
    property bool loaded: false

    function reload() {
        const data = page.ipset
        page.form = {
            blocked: data.blocked || "",
            unblocked: data.unblocked || "",
            telegram: data.telegram || ""
        }
        page.filterMode = data.filterMode || "none"
        page.loaded = true
    }

    function setField(name, value) {
        const next = Object.assign({}, page.form)
        next[name] = value
        page.form = next
    }

    function save() {
        Backend.saveIpset({
            blocked: page.form.blocked,
            unblocked: page.form.unblocked,
            telegram: page.form.telegram
        })
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshIpset()
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
                    KeyNavigation.down: tabBlocked
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "Настройки IPSet"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    Text {
                        text: "IP, подсети и диапазоны · пишутся в files/lists/ipset-*_user.txt"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
            }

            // --- Вкладки ---------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.xs

                MButton {
                    id: tabBlocked
                    text: "Заблокированные (" + (page.ipset.blockedCount || 0) + ")"
                    variant: page.tab === "blocked" ? "filled" : "text"
                    onClicked: page.tab = "blocked"
                    KeyNavigation.right: tabUnblocked
                    KeyNavigation.down: areaBlocked
                }

                MButton {
                    id: tabUnblocked
                    text: "Исключения (" + (page.ipset.unblockedCount || 0) + ")"
                    variant: page.tab === "unblocked" ? "filled" : "text"
                    onClicked: page.tab = "unblocked"
                    KeyNavigation.left: tabBlocked
                    KeyNavigation.right: tabTelegram
                    KeyNavigation.down: areaBlocked
                }

                MButton {
                    id: tabTelegram
                    text: "Telegram (" + (page.ipset.telegramCount || 0) + ")"
                    variant: page.tab === "telegram" ? "filled" : "text"
                    onClicked: page.tab = "telegram"
                    KeyNavigation.left: tabUnblocked
                    KeyNavigation.down: areaBlocked
                }

                Item { Layout.fillWidth: true }
            }

            // --- Поле текущей вкладки -------------------------------------
            Card {
                Layout.fillWidth: true
                title: page.tab === "blocked" ? "Заблокированные диапазоны"
                    : page.tab === "unblocked" ? "Исключения" : "Диапазоны Telegram"
                subtitle: page.tab === "blocked" ? "ipset-all_user.txt"
                    : page.tab === "unblocked" ? "ipset-exclude_user.txt"
                    : "ipset-telegram_user.txt"

                TextArea {
                    id: areaBlocked
                    width: parent.width
                    height: 220
                    wrapMode: TextArea.WrapAnywhere
                    selectByMouse: true
                    color: Theme.colors.on_surface
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_small")
                    text: !page.loaded ? ""
                        : page.tab === "blocked" ? page.form.blocked
                        : page.tab === "unblocked" ? page.form.unblocked
                        : page.form.telegram
                    onTextChanged: {
                        if (!page.loaded) {
                            return
                        }
                        const name = page.tab
                        if (page.form[name] !== text) {
                            page.setField(name, text)
                        }
                    }
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: areaBlocked.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                Text {
                    width: parent.width
                    text: "Формат: 1.2.3.4 · 10.0.0.0/8 · 2001:db8::/32 · 172.16.0.0-172.31.255.255. "
                        + "Строки с # — комментарии."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: saveButton
                        text: "Сохранить диапазоны"
                        variant: "filled"
                        iconSource: "../icons/next.svg"
                        onClicked: page.save()
                        KeyNavigation.up: areaBlocked
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "При ошибке в IP ничего не сохраняется."
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                        elide: Text.ElideRight
                    }
                }
            }

            // --- Режим фильтра --------------------------------------------
            Card {
                Layout.fillWidth: true
                title: "Режим фильтра ipset-all.txt"
                subtitle: "Что подставляется в {ipset_all} при запуске службы"

                Column {
                    width: parent.width
                    spacing: Theme.spaces.xs

                    MRadioButton {
                        text: "any — файл пустой, фильтр не ограничивает адреса"
                        checked: page.filterMode === "any"
                        onClicked: page.filterMode = "any"
                    }

                    MRadioButton {
                        text: "loaded — диапазоны из utils/ipset-all.txt"
                        checked: page.filterMode === "loaded"
                        onClicked: page.filterMode = "loaded"
                    }

                    MRadioButton {
                        text: "none — только заглушка 203.0.113.113/32"
                        checked: page.filterMode === "none"
                        onClicked: page.filterMode = "none"
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        id: applyFilterButton
                        text: "Применить режим"
                        variant: "tonal"
                        onClicked: Backend.applyIpsetFilter(page.filterMode)
                        KeyNavigation.up: areaBlocked
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "Файл перезаписывается с бэкапом, служба перезапускается."
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                        elide: Text.ElideRight
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    Connections {
        target: Backend

        function onIpsetChanged() {
            if (!page.loaded) {
                page.reload()
            }
        }
    }
}
