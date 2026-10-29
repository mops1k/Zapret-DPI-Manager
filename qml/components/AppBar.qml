import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Верхняя панель приложения: статус службы и действия.
RowLayout {
    id: root

    signal restartClicked()
    signal gameFilterClicked()
    signal settingsClicked()
    signal guideClicked()
    signal infoClicked()

    property string serviceState: "unknown"
    property bool active: false
    property string stateLabel: ""
    property bool busy: false

    // Куда ведёт навигация «вниз» из панели — задаёт страница.
    property Item navDown: null

    property alias restartButton: refreshBtn
    property alias gameFilterButton: gameFilterBtn
    property alias settingsButton: settingsBtn
    property alias guideButton: guideBtn
    property alias infoButton: infoBtn

    spacing: Theme.spaces.sm

    // Индикатор состояния службы.
    Rectangle {
        Layout.alignment: Qt.AlignVCenter
        implicitWidth: 12
        implicitHeight: 12
        radius: width / 2
        color: root.active ? Theme.colors.success
            : root.serviceState === "failed" ? Theme.colors.error
            : root.serviceState === "activating"
                || root.serviceState === "deactivating" ? Theme.colors.warning
            : Theme.colors.error
        Behavior on color { ColorAnimation { duration: Theme.durations.medium } }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 0

        Text {
            text: "Zapret DPI Manager"
            color: Theme.colors.on_surface
            font.pixelSize: Theme.typeSize("title_large")
            font.weight: Font.Medium
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        Text {
            text: root.stateLabel.length > 0
                ? "Обход блокировок DPI · " + root.stateLabel
                : "Обход блокировок DPI"
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_small")
            elide: Text.ElideRight
            Layout.fillWidth: true
        }
    }

    MButton {
        id: refreshBtn
        variant: "icon"
        iconSource: "../icons/refresh.svg"
        dense: true
        enabled: !root.busy
        onClicked: root.restartClicked()
        ToolTip.visible: hovered
        ToolTip.text: "Перезапустить службу"
        KeyNavigation.right: gameFilterBtn
        KeyNavigation.down: root.navDown
    }

    MButton {
        id: gameFilterBtn
        variant: "icon"
        iconSource: "../icons/keyboard.svg"
        dense: true
        onClicked: root.gameFilterClicked()
        ToolTip.visible: hovered
        ToolTip.text: "GameFilter"
        KeyNavigation.left: refreshBtn
        KeyNavigation.right: settingsBtn
        KeyNavigation.down: root.navDown
    }

    MButton {
        id: settingsBtn
        variant: "icon"
        iconSource: "../icons/settings.svg"
        dense: true
        onClicked: root.settingsClicked()
        ToolTip.visible: hovered
        ToolTip.text: "Настройки"
        KeyNavigation.left: gameFilterBtn
        KeyNavigation.right: guideBtn
        KeyNavigation.down: root.navDown
    }

    MButton {
        id: guideBtn
        variant: "icon"
        iconSource: "../icons/guide.svg"
        dense: true
        onClicked: root.guideClicked()
        ToolTip.visible: hovered
        ToolTip.text: "Руководство пользователя"
        KeyNavigation.left: settingsBtn
        KeyNavigation.right: infoBtn
        KeyNavigation.down: root.navDown
    }

    MButton {
        id: infoBtn
        variant: "icon"
        iconSource: "../icons/info.svg"
        dense: true
        onClicked: root.infoClicked()
        ToolTip.visible: hovered
        ToolTip.text: "О программе"
        KeyNavigation.left: guideBtn
        KeyNavigation.down: root.navDown
    }
}
