import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран конструктора своей стратегии по категориям.
Item {
    id: page

    signal back()

    property var selections: ({})
    property int revision: 1

    readonly property var categories: Backend.strategyCategories

    function selectedFor(category) {
        const value = page.selections[category]
        return value === undefined ? "Не выбирать" : value
    }

    function setSelected(category, value) {
        const next = Object.assign({}, page.selections)
        next[category] = value
        page.selections = next
        page.revision++
    }

    function commandText(category) {
        page.revision // пересчитываем при смене выбора
        const value = page.selectedFor(category)
        if (value === "Не выбирать") {
            return ""
        }
        return Backend.commandFor(category, value)
    }

    function applySelection() {
        Backend.applyCustomStrategy(page.selections)
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.reloadCustomSelections()
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
                    KeyNavigation.right: applyButton
                    KeyNavigation.down: firstSelectable
                    KeyNavigation.left: backButton
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "Своя стратегия"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        text: "Выберите вариант в каждой категории — пустые пропускаются"
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                BusyIndicator {
                    running: Backend.strategyBusy
                    visible: Backend.strategyBusy
                    implicitWidth: 22
                    implicitHeight: 22
                }
            }

            Repeater {
                id: categoriesRepeater
                model: page.categories

                delegate: Card {
                    id: categoryCard

                    Layout.fillWidth: true
                    title: modelData.name
                    subtitle: ""

                    property alias combo: categoryBox

                    ComboBox {
                        id: categoryBox
                        width: parent.width
                        model: ["Не выбирать"].concat(modelData.options)
                        currentIndex: {
                            const current = page.selectedFor(modelData.name)
                            const index = model.indexOf(current)
                            return index >= 0 ? index : 0
                        }
                        onActivated: page.setSelected(modelData.name,
                                                       model[currentIndex])

                        KeyNavigation.up: index === 0 ? backButton
                            : categoriesRepeater.itemAt(index - 1).combo
                        KeyNavigation.down: index === categoriesRepeater.count - 1
                            ? applyButton
                            : categoriesRepeater.itemAt(index + 1).combo

                        background: Rectangle {
                            radius: Theme.radii.sm
                            color: Theme.colors.surface_container_high
                            border.width: 1
                            border.color: categoryBox.activeFocus ? Theme.colors.primary
                                : Theme.colors.outline_variant
                        }

                        contentItem: Text {
                            leftPadding: Theme.spaces.sm
                            verticalAlignment: Text.AlignVCenter
                            text: categoryBox.displayText
                            color: Theme.colors.on_surface
                            font.pixelSize: Theme.typeSize("body_large")
                            elide: Text.ElideRight
                        }
                    }

                    Text {
                        width: parent.width
                        visible: text.length > 0
                        text: page.commandText(modelData.name)
                        color: Theme.colors.on_surface_variant
                        font.pixelSize: Theme.typeSize("body_small")
                        font.family: "monospace"
                        wrapMode: Text.WrapAnywhere
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Text {
                    Layout.fillWidth: true
                    text: "Команды пишутся в config.txt, служба перезапускается"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    elide: Text.ElideRight
                }

                MButton {
                    id: applyButton
                    text: Backend.strategyBusy ? "Применение…" : "Сохранить и применить"
                    variant: "filled"
                    iconSource: "../icons/next.svg"
                    enabled: !Backend.strategyBusy
                    onClicked: page.applySelection()
                    KeyNavigation.left: backButton
                    KeyNavigation.up: categoriesRepeater.count > 0
                        ? categoriesRepeater.itemAt(categoriesRepeater.count - 1) : backButton
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    //: Первая карточка с выбором — цель навигации «вниз» из шапки.
    property Item firstSelectable: categoriesRepeater.count > 0
        ? categoriesRepeater.itemAt(0).combo : applyButton

    Connections {
        target: Backend

        function onCustomSelectionsChanged() {
            page.selections = Object.assign({}, Backend.customSelections)
        }
    }
}
