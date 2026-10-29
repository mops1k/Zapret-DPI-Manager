import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Разблокировка сервисов через /etc/hosts.
Item {
    id: page

    signal back()

    readonly property var state: Backend.unlockState
    readonly property var categories: state.categories !== undefined ? state.categories : []

    property var selected: ({})
    property bool loaded: false

    function reload() {
        const next = {}
        for (let i = 0; i < page.categories.length; i++) {
            const item = page.categories[i]
            next[item.id] = item.selected === true
        }
        page.selected = next
        page.loaded = true
    }

    function toggle(id) {
        const next = Object.assign({}, page.selected)
        next[id] = !next[id]
        page.selected = next
    }

    function selectedIds() {
        const ids = []
        for (const id in page.selected) {
            if (page.selected[id]) {
                ids.push(id)
            }
        }
        return ids
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshUnlock()
        focusTimer.start()
    }

    onVisibleChanged: {
        if (visible) {
            Backend.refreshUnlock()
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
                KeyNavigation.down: categoryFlick
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Разблокировка сервисов"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Записи добавляются в /etc/hosts отдельным блоком · нужен sudo"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        Flickable {
            id: categoryFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: categoryColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: categoryColumn
                width: categoryFlick.width
                spacing: 2

                Repeater {
                    model: page.categories

                    delegate: MCheckBox {
                        width: categoryColumn.width
                        text: modelData.name + "  ·  доменов: " + modelData.count
                        checked: page.selected[modelData.id] === true
                        onClicked: page.toggle(modelData.id)
                    }
                }

                Text {
                    visible: page.categories.length === 0
                    text: "Категории не найдены"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: "Выбрано категорий: " + page.selectedIds().length
                + " · снятие всех галочек удаляет наши записи (бэкап /etc/hosts сохраняется)."
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_small")
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                id: applyButton
                text: "Применить"
                variant: "filled"
                onClicked: Backend.saveUnlock(page.selectedIds())
                KeyNavigation.right: clearButton
                KeyNavigation.up: categoryFlick
            }

            MButton {
                id: clearButton
                text: "Снять всё"
                variant: "outlined"
                onClicked: {
                    const next = {}
                    for (const id in page.selected) {
                        next[id] = false
                    }
                    page.selected = next
                }
                KeyNavigation.left: applyButton
            }

            Item { Layout.fillWidth: true }
        }
    }

    Connections {
        target: Backend

        function onUnlockChanged() {
            if (!page.loaded) {
                page.reload()
            }
        }
    }
}
