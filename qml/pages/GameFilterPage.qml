import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// GameFilter: включение отдельного фильтра и игровые пресеты.
Item {
    id: page

    signal back()

    readonly property var state: Backend.gameFilterState
    readonly property var presets: state.presets !== undefined ? state.presets : []

    property string mode: "both"
    property string selectedPreset: ""
    property bool loaded: false

    function reload() {
        page.mode = page.state.mode || "both"
        page.selectedPreset = page.state.activePreset || ""
        page.loaded = true
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshGameFilter()
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
                KeyNavigation.down: toggleButton
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "GameFilter"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Игровые порты ({GameFilter}) и пресеты игр"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }
        }

        // Состояние фильтра.
        Card {
            Layout.fillWidth: true
            title: "Отдельный GameFilter"
            subtitle: "Порты {GameFilter} в службе"

            Text {
                width: parent.width
                text: page.state.enabled ? "Включён · режим " + page.mode.toUpperCase()
                    : "Выключен"
                color: page.state.enabled ? Theme.colors.success : Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_large")
            }

            RowLayout {
                width: parent.width
                spacing: Theme.spaces.sm

                MButton {
                    id: toggleButton
                    text: page.state.enabled ? "Выключить" : "Включить"
                    variant: "filled"
                    onClicked: {
                        if (page.state.enabled) {
                            Backend.setGameFilter(false, page.mode)
                        } else {
                            warningDialog.open()
                        }
                    }
                    KeyNavigation.down: modeBoth
                }

                Text {
                    Layout.fillWidth: true
                    text: page.state.enabled
                        ? "При включении активный пресет игры снимается."
                        : "GameFilter и пресет игры не используются одновременно."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    wrapMode: Text.WordWrap
                }
            }

            Column {
                width: parent.width
                spacing: Theme.spaces.xs

                MRadioButton {
                    id: modeBoth
                    text: "TCP + UDP — оба протокола"
                    checked: page.mode === "both"
                    onClicked: page.mode = "both"
                }

                MRadioButton {
                    text: "Только TCP"
                    checked: page.mode === "tcp"
                    onClicked: page.mode = "tcp"
                }

                MRadioButton {
                    text: "Только UDP"
                    checked: page.mode === "udp"
                    onClicked: page.mode = "udp"
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    MButton {
                        text: "Применить режим"
                        variant: "tonal"
                        enabled: page.state.enabled === true
                        onClicked: Backend.setGameFilter(true, page.mode)
                    }
                }
            }
        }

        // Пресеты игр.
        Card {
            Layout.fillWidth: true
            title: "Пресеты игр"
            subtitle: "Строки и порты добавляются в config.txt"

            Flickable {
                id: presetFlick
                width: parent.width
                height: Math.min(180, presetsColumn.implicitHeight)
                clip: true
                contentWidth: width
                contentHeight: presetsColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                Column {
                    id: presetsColumn
                    width: presetFlick.width
                    spacing: 2

                    Repeater {
                        model: page.presets

                        delegate: MRadioButton {
                            width: presetsColumn.width
                            text: modelData.name
                                + (modelData.id === page.state.activePreset ? "  (активен)" : "")
                            checked: page.selectedPreset === modelData.id
                            onClicked: page.selectedPreset = modelData.id
                        }
                    }

                    Text {
                        visible: page.presets.length === 0
                        text: "Пресеты не найдены"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_medium")
                    }
                }
            }

            RowLayout {
                width: parent.width
                spacing: Theme.spaces.sm

                MButton {
                    id: applyPresetButton
                    text: "Применить пресет"
                    variant: "filled"
                    enabled: page.selectedPreset.length > 0
                    onClicked: Backend.applyGamePreset(page.selectedPreset)
                    KeyNavigation.right: clearPresetButton
                }

                MButton {
                    id: clearPresetButton
                    text: "Снять пресет"
                    variant: "outlined"
                    enabled: (page.state.activePreset || "").length > 0
                    onClicked: Backend.clearGamePreset()
                    KeyNavigation.left: applyPresetButton
                }

                Text {
                    Layout.fillWidth: true
                    text: (page.state.activePreset || "").length > 0
                        ? "Активен: " + page.state.activePreset
                        : "Пресет не выбран"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    elide: Text.ElideRight
                }
            }

            Text {
                width: parent.width
                text: "Пресет Fall Guys — только для версии из Epic Games Store; "
                    + "в Steam смените регион игры на США (восток)."
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }
        }

        Item { Layout.fillHeight: true }
    }

    //: Предупреждение перед включением GameFilter (тексты Tk-версии).
    Dialog {
        id: warningDialog
        objectName: "gameFilterWarning"

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
            spacing: Theme.spaces.sm

            Text {
                Layout.fillWidth: true
                text: "ВНИМАНИЕ!"
                color: Theme.colors.warning
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: "Данный фильтр является экспериментальной функцией"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                text: "Возможные проблемы на Steam Deck:"
                color: Theme.colors.error
                font.pixelSize: Theme.typeSize("body_small")
            }

            Text {
                Layout.fillWidth: true
                text: "• черный экран после перехода с рабочего стола в игровой режим. "
                        + "Лучше перезагрузиться\n"
                    + "• долгая загрузка после включения/перезагрузки\n"
                    + "• скорее всего не будет работать Youtube и Discord. "
                        + "Нужно будет отключать GameFilter\n"
                    + "• возможны другие нестабильности в работе"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                text: "Пользоваться данной функцией на свой страх и риск"
                color: Theme.colors.warning
                font.pixelSize: Theme.typeSize("label_large")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Item { Layout.fillWidth: true }

                MButton {
                    text: "Назад"
                    variant: "text"
                    onClicked: warningDialog.close()
                }

                MButton {
                    text: "Включить"
                    variant: "filled"
                    onClicked: {
                        warningDialog.close()
                        Backend.setGameFilter(true, page.mode)
                    }
                }
            }
        }
    }

    Connections {
        target: Backend

        function onGameFilterChanged() {
            if (!page.loaded) {
                page.reload()
            }
        }
    }
}
