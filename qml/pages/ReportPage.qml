import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Отчёт автоподбора: список отчётов и таблица стратегий с целями.
Item {
    id: page

    signal back()

    readonly property var reports: Backend.reports
    readonly property var reportData: Backend.reportData
    readonly property var rows: reportData.rows !== undefined ? reportData.rows : []

    property string currentPath: ""

    function verdictColor(verdict) {
        switch (verdict) {
        case "working": return Theme.colors.success
        case "partial": return Theme.colors.warning
        case "failed": return Theme.colors.error
        default: return Theme.colors.on_surface_variant
        }
    }

    function flag(value) {
        if (value === true) {
            return "✓"
        }
        if (value === false) {
            return "✗"
        }
        return "—"
    }

    Keys.onEscapePressed: page.back()

    Component.onCompleted: {
        Backend.refreshReports()
        focusTimer.start()
    }

    onVisibleChanged: {
        if (visible) {
            Backend.refreshReports()
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
                KeyNavigation.down: reportList
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Text {
                    text: "Отчёт автоподбора"
                    color: Theme.colors.on_surface
                    font.pixelSize: Theme.typeSize("title_large")
                    font.weight: Font.Medium
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    text: "Данные из utils/reports (*.json)"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_small")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
            }

            MButton {
                text: "Обновить список"
                variant: "outlined"
                onClicked: Backend.refreshReports()
            }
        }

        // Список отчётов.
        Flickable {
            id: reportFlick
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(120, reportColumn.implicitHeight)
            clip: true
            contentWidth: width
            contentHeight: reportColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: reportColumn
                width: reportFlick.width
                spacing: 2

                Repeater {
                    model: page.reports

                    delegate: MButton {
                        id: reportList
                        width: reportColumn.width
                        text: modelData.name + " · " + modelData.mode + " · стратегий: "
                            + modelData.strategies
                        variant: page.currentPath === modelData.path ? "tonal" : "text"
                        onClicked: {
                            page.currentPath = modelData.path
                            Backend.loadReport(modelData.path)
                        }
                    }
                }

                Text {
                    visible: page.reports.length === 0
                    text: "Отчётов пока нет — запустите автоподбор стратегий."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                }
            }
        }

        // Таблица стратегий.
        Text {
            Layout.fillWidth: true
            visible: page.rows.length > 0
            text: "Отчёт: " + (page.reportData.generatedAt || page.currentPath)
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_small")
            elide: Text.ElideMiddle
        }

        RowLayout {
            Layout.fillWidth: true
            visible: page.rows.length > 0
            spacing: Theme.spaces.sm

            Text {
                Layout.fillWidth: true
                text: "Стратегия"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
            }
            Text {
                Layout.preferredWidth: 80
                horizontalAlignment: Text.AlignRight
                text: "Успех"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
            }
            Text {
                Layout.preferredWidth: 90
                horizontalAlignment: Text.AlignHCenter
                text: "YT · DC · TG"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
            }
            Text {
                Layout.preferredWidth: 110
                horizontalAlignment: Text.AlignHCenter
                text: "Вердикт"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_small")
            }
        }

        Flickable {
            id: tableFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: tableColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: tableColumn
                width: tableFlick.width
                spacing: 2

                Repeater {
                    model: page.rows

                    delegate: Rectangle {
                        width: tableColumn.width
                        height: 32
                        color: index % 2 === 0 ? Theme.colors.surface_container_low : "transparent"
                        radius: Theme.radii.xs

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.spaces.sm
                            anchors.rightMargin: Theme.spaces.sm
                            spacing: Theme.spaces.sm

                            Text {
                                Layout.fillWidth: true
                                text: modelData.name
                                color: Theme.colors.on_surface
                                font.pixelSize: Theme.typeSize("body_medium")
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.preferredWidth: 80
                                horizontalAlignment: Text.AlignRight
                                text: modelData.successRate.toFixed(0) + "%"
                                color: Theme.colors.on_surface
                                font.pixelSize: Theme.typeSize("body_medium")
                            }
                            Text {
                                Layout.preferredWidth: 90
                                horizontalAlignment: Text.AlignHCenter
                                text: page.flag(modelData.youtube) + " · "
                                    + page.flag(modelData.discord) + " · "
                                    + page.flag(modelData.telegram)
                                color: Theme.colors.on_surface_variant
                                font.pixelSize: Theme.typeSize("body_small")
                            }
                            Text {
                                Layout.preferredWidth: 110
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData.verdictLabel
                                color: page.verdictColor(modelData.verdict)
                                font.pixelSize: Theme.typeSize("body_medium")
                            }
                        }
                    }
                }

                Text {
                    visible: page.rows.length === 0
                    text: "Выберите отчёт в списке сверху."
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                }
            }
        }
    }

    Connections {
        target: Backend

        function onReportDataChanged() {
            if (page.rows.length > 0 && page.currentPath === "") {
                page.currentPath = page.reportData.path || ""
            }
        }
    }
}
