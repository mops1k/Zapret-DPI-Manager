import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Автоподбор стратегий: перебор стратегий с проверкой сервисов и отчётом.
Item {
    id: page

    signal back()
    signal openReport()

    readonly property var lines: Backend.testerLog
    readonly property bool running: Backend.testerRunning
    readonly property var allStrategies: Backend.strategies

    property var selected: ({})
    property string mode: "standard"
    property bool loaded: false

    function kindColor(kind) {
        switch (kind) {
        case "success": return Theme.colors.success
        case "warning": return Theme.colors.warning
        case "error": return Theme.colors.error
        case "primary": return Theme.colors.primary
        default: return Theme.colors.on_surface
        }
    }

    function selectAll(value) {
        const next = {}
        for (let i = 0; i < page.allStrategies.length; i++) {
            next[page.allStrategies[i].name] = value
        }
        page.selected = next
        page.loaded = true
    }

    function start() {
        const names = []
        for (const name in page.selected) {
            if (page.selected[name]) {
                names.push(name)
            }
        }
        Backend.startStrategyTest(page.mode, names)
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshStrategies()
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
                KeyNavigation.down: modeBox
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Автоподбор стратегий"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Перебор стратегий с проверкой YouTube, Discord и Telegram"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }

            BusyIndicator {
                running: page.running
                visible: page.running
                implicitWidth: 22
                implicitHeight: 22
            }
        }

        // Параметры запуска.
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            ComboBox {
                id: modeBox
                Layout.preferredWidth: 320
                model: Backend.testerModes
                textRole: "label"
                enabled: !page.running
                currentIndex: 0
                onActivated: {
                    const items = Backend.testerModes
                    page.mode = items[currentIndex].value
                }
                indicator: Text {
                    x: modeBox.width - width - Theme.spaces.md
                    y: (modeBox.height - height) / 2
                    text: "▾"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: 14
                }
                background: Rectangle {
                    radius: Theme.radii.sm
                    color: Theme.colors.surface_container_high
                    border.width: 1
                    border.color: modeBox.activeFocus ? Theme.colors.primary
                        : Theme.colors.outline_variant
                }
                contentItem: Text {
                    leftPadding: Theme.spaces.sm
                    verticalAlignment: Text.AlignVCenter
                    text: modeBox.displayText
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("body_large")
                    elide: Text.ElideRight
                }
                KeyNavigation.down: startButton
            }

            MButton {
                id: startButton
                text: page.running ? "Остановить тест" : "Запустить тест"
                variant: "filled"
                iconSource: page.running ? "../icons/close.svg" : "../icons/play.svg"
                onClicked: page.running ? Backend.stopStrategyTest() : page.start()
                KeyNavigation.right: applyButton
                KeyNavigation.up: modeBox
            }

            MButton {
                id: reportButton
                text: "Отчёт"
                variant: "outlined"
                enabled: Backend.testerReport.length > 0
                onClicked: page.openReport()
            }

            MButton {
                id: applyButton
                text: "Применить лучшую"
                variant: "tonal"
                enabled: !page.running && Backend.testerBest.length > 0
                onClicked: Backend.applyBestStrategy(Backend.testerBest)
                KeyNavigation.left: startButton
            }

            Text {
                Layout.fillWidth: true
                text: Backend.testerSummary.length > 0 ? Backend.testerSummary
                    : "Выберите стратегии и запустите перебор"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                elide: Text.ElideRight
            }
        }

        // Список стратегий для теста.
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            MButton {
                text: "Выбрать все"
                variant: "text"
                enabled: !page.running
                onClicked: page.selectAll(true)
            }

            MButton {
                text: "Снять все"
                variant: "text"
                enabled: !page.running
                onClicked: page.selectAll(false)
            }

            Text {
                Layout.fillWidth: true
                text: "Стратегий для теста: " + Object.keys(page.selected).filter(function (k) {
                    return page.selected[k]
                }).length
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                elide: Text.ElideRight
            }
        }

        Flickable {
            id: listFlick
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(150, contentHeight)
            clip: true
            contentWidth: width
            contentHeight: listColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: listColumn
                width: listFlick.width
                spacing: 2

                Repeater {
                    model: page.allStrategies

                    delegate: MCheckBox {
                        width: listColumn.width
                        text: modelData.name
                        checked: page.selected[modelData.name] === true
                        enabled: !page.running
                        onClicked: {
                            const next = Object.assign({}, page.selected)
                            next[modelData.name] = !next[modelData.name]
                            page.selected = next
                            page.loaded = true
                        }
                    }
                }
            }
        }

        // Лог теста.
        Flickable {
            id: logFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: logColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            onContentHeightChanged: contentY = Math.max(0, contentHeight - height)

            Rectangle {
                anchors.fill: parent
                radius: Theme.radii.md
                color: Theme.colors.surface_container_low
                border.width: 1
                border.color: Theme.colors.outline_variant
            }

            Column {
                id: logColumn

                width: logFlick.width - Theme.spaces.md * 2
                x: Theme.spaces.md
                y: Theme.spaces.md
                spacing: 2

                Repeater {
                    model: page.lines

                    delegate: Text {
                        width: logColumn.width
                        text: modelData.text
                        color: page.kindColor(modelData.kind)
                        font.family: "monospace"
                        font.pixelSize: Theme.typeSize("body_small")
                        wrapMode: Text.WordWrap
                    }
                }

                Text {
                    visible: page.lines.length === 0
                    text: "Лог тестирования появится здесь. Тест перезапускает службу, "
                        + "поэтому нужен пароль sudo."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    width: logColumn.width
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    Connections {
        target: Backend

        function onStrategiesChanged() {
            if (Object.keys(page.selected).length === 0) {
                page.selectAll(true)
            }
        }
    }
}
