import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран выбора готовой стратегии.
Item {
    id: page

    signal back()
    signal openCustom()
    signal openBbdpi()
    signal openTester()

    property string selectedName: ""
    readonly property int columns: Math.max(1, Math.floor((flick.width - 2 * Theme.spaces.xl)
        / 250))
    //: Высота столбца при заполнении сверху вниз (как в Tk-версии).
    readonly property int perColumn: Backend.strategies.length === 0 ? 0
        : Math.ceil(Backend.strategies.length / columns)
    //: Список, переставленный в порядок «по столбцам»: 1,2,3 вниз, затем 4,5,6.
    readonly property var orderedStrategies: {
        const source = Backend.strategies
        const cols = page.columns
        const perCol = page.perColumn
        if (perCol <= 0) {
            return source
        }
        const ordered = []
        for (let slot = 0; slot < source.length; slot++) {
            // Позиция слота в сетке: row-major (слева направо, потом вниз),
            // а элементы берём по столбцам: 1,2,3 вниз, затем 4,5,6.
            const row = Math.floor(slot / cols)
            const col = slot % cols
            const index = col * perCol + row
            if (index < source.length) {
                ordered.push(source[index])
            }
        }
        return ordered
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshStrategies()
        page.selectedName = Backend.strategyName
        focusTimer.start()
    }

    //: StackView анимирует переход: фокус ставим после появления страницы.
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

            // Шапка экрана
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
                    KeyNavigation.right: tabReady
                    KeyNavigation.down: strategyRepeater.count > 0
                        ? strategyRepeater.itemAt(0) : applyButton
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "Смена стратегии"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        text: "Отмеченные ★ уже показали себя рабочими"
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

            // Вкладки источников стратегий
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.xs

                MButton {
                    id: tabReady
                    text: "Готовые"
                    variant: "filled"
                    KeyNavigation.left: backButton
                    KeyNavigation.right: tabCustom
                    KeyNavigation.down: strategyRepeater.count > 0
                        ? strategyRepeater.itemAt(0) : applyButton
                }

                MButton {
                    id: tabCustom
                    text: "Свой пресет"
                    variant: "text"
                    onClicked: page.openCustom()
                    KeyNavigation.left: tabReady
                    KeyNavigation.right: tabBbdpi
                    KeyNavigation.down: strategyRepeater.count > 0
                        ? strategyRepeater.itemAt(0) : applyButton
                }

                MButton {
                    id: tabBbdpi
                    text: "BB DPI"
                    variant: "text"
                    onClicked: page.openBbdpi()
                    KeyNavigation.left: tabCustom
                    KeyNavigation.down: strategyRepeater.count > 0
                        ? strategyRepeater.itemAt(0) : applyButton
                }

                Item { Layout.fillWidth: true }
            }

            // Список готовых стратегий
            Card {
                Layout.fillWidth: true
                title: "Готовые стратегии"
                subtitle: "Файлы из files/strategy"

                Text {
                    width: parent.width
                    visible: Backend.strategies.length === 0
                    text: "Стратегии не найдены в " + Backend.managerDir + "/files/strategy"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    wrapMode: Text.WordWrap
                }

                Grid {
                    id: strategyGrid
                    width: parent.width
                    columns: page.columns
                    spacing: Theme.spaces.xs

                    Repeater {
                        id: strategyRepeater
                        model: page.orderedStrategies

                        delegate: StrategyRow {
                            id: rowItem

                            width: (strategyGrid.width
                                - strategyGrid.spacing * (strategyGrid.columns - 1))
                                / strategyGrid.columns
                            strategyName: modelData.name
                            working: modelData.working
                            current: modelData.name === Backend.strategyName
                            selected: modelData.name === page.selectedName
                            onClicked: {
                                page.selectedName = modelData.name
                                rowItem.forceActiveFocus()
                            }

                            // Сетка row-major: вниз/вверх — соседняя строка (±columns),
                            // влево/вправо — сосед в строке (элементы разложены по столбцам).
                            KeyNavigation.up: index - strategyGrid.columns >= 0
                                ? strategyRepeater.itemAt(index - strategyGrid.columns)
                                : tabReady
                            KeyNavigation.down: index + strategyGrid.columns
                                < strategyRepeater.count
                                ? strategyRepeater.itemAt(index + strategyGrid.columns)
                                : applyButton
                            KeyNavigation.left: index % strategyGrid.columns > 0
                                ? strategyRepeater.itemAt(index - 1) : null
                            KeyNavigation.right: (index + 1) % strategyGrid.columns !== 0
                                && index + 1 < strategyRepeater.count
                                ? strategyRepeater.itemAt(index + 1) : null
                        }
                    }
                }
            }


            // Подвал: выбор и применение
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Text {
                    Layout.fillWidth: true
                    text: page.selectedName.length > 0
                        ? "Выбрано: " + page.selectedName
                        : "Выберите стратегию"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    elide: Text.ElideRight
                }

                MButton {
                    id: testerButton
                    text: "Автоподбор"
                    variant: "outlined"
                    iconSource: "../icons/connection.svg"
                    onClicked: page.openTester()
                }

                MButton {
                    id: applyButton
                    text: Backend.strategyBusy ? "Применение…" : "Применить"
                    variant: "filled"
                    iconSource: "../icons/next.svg"
                    enabled: page.selectedName.length > 0 && !Backend.strategyBusy
                    onClicked: Backend.applyStrategy(page.selectedName)
                    KeyNavigation.up: strategyRepeater.count > 0
                        ? strategyRepeater.itemAt(strategyRepeater.count - 1) : tabReady
                    KeyNavigation.left: backButton
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    Connections {
        target: Backend

        function onStrategyNameChanged() {
            if (page.selectedName.length === 0) {
                page.selectedName = Backend.strategyName
            }
        }
    }
}
