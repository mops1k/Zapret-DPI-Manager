import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Экран «Стратегия из BB DPI»: строка ciadpi → аргументы nfqws.
Item {
    id: page

    signal back()

    readonly property var result: Backend.bbdpiResult
    readonly property bool hasResult: result && result.text !== undefined
        && String(result.text).length > 0
    readonly property bool canApply: hasResult && result.ok === true
        && (result.problems === undefined || result.problems.length === 0)

    Keys.onEscapePressed: page.back()

    Component.onCompleted: focusTimer.start()
    onVisibleChanged: if (visible) focusTimer.start()

    Timer {
        id: focusTimer
        interval: 60
        onTriggered: backButton.forceActiveFocus()
    }

    function convertNow() {
        Backend.convertBbdpi(commandArea.text, hostlistCheck.checked)
    }

    function applyNow() {
        const unsupported = result.unsupported === undefined ? [] : result.unsupported
        if (unsupported.length > 0) {
            confirmDialog.open()
            return
        }
        Backend.applyBbdpi(nameField.text, commandArea.text, hostlistCheck.checked)
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
                    KeyNavigation.down: commandArea
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        text: "Стратегия из BB DPI"
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("title_large")
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        text: "Строка ciadpi (ByeByeDPI) преобразуется в аргументы nfqws"
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

            Card {
                Layout.fillWidth: true
                title: "Строка BB DPI"
                subtitle: "Например: -s1 -f-1 -t8 -S"

                TextArea {
                    id: commandArea
                    width: parent.width
                    height: 76
                    wrapMode: TextArea.Wrap
                    selectByMouse: true
                    placeholderText: "-s1 -f-1 -t8 -S"
                    color: Theme.colors.on_surface
                    placeholderTextColor: Theme.colors.on_surface_variant
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_medium")
                    background: Rectangle {
                        radius: Theme.radii.sm
                        color: Theme.colors.surface_container_high
                        border.width: 1
                        border.color: commandArea.activeFocus ? Theme.colors.primary
                            : Theme.colors.outline_variant
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spaces.sm

                    TextField {
                        id: nameField
                        Layout.fillWidth: true
                        text: "BBDI_Custom"
                        selectByMouse: true
                        color: Theme.colors.on_surface
                        font.pixelSize: Theme.typeSize("body_large")
                        background: Rectangle {
                            radius: Theme.radii.sm
                            color: Theme.colors.surface_container_high
                            border.width: 1
                            border.color: nameField.activeFocus ? Theme.colors.primary
                                : Theme.colors.outline_variant
                        }
                    }

                    MButton {
                        id: convertButton
                        text: "Преобразовать"
                        variant: "tonal"
                        enabled: !Backend.strategyBusy && commandArea.text.length > 0
                        onClicked: page.convertNow()
                    }
                }

                CheckBox {
                    id: hostlistCheck
                    text: "Ограничить нашим списком доменов"
                    checked: false
                    font.pixelSize: Theme.typeSize("body_medium")
                    palette.windowText: Theme.colors.on_surface
                    palette.text: Theme.colors.on_surface
                }
            }

            Card {
                Layout.fillWidth: true
                visible: page.hasResult
                title: "Результат"
                subtitle: "Готово для config.txt"

                Text {
                    width: parent.width
                    text: page.hasResult ? String(page.result.text) : ""
                    color: page.result.ok === true ? Theme.colors.on_surface
                        : Theme.colors.error
                    font.family: "monospace"
                    font.pixelSize: Theme.typeSize("body_small")
                    wrapMode: Text.WrapAnywhere
                }

                Text {
                    width: parent.width
                    visible: page.result.problems !== undefined
                        && page.result.problems.length > 0
                    text: page.result.problems === undefined ? ""
                        : "nfqws не принимает результат:\n• " + page.result.problems.join("\n• ")
                    color: Theme.colors.error
                    font.pixelSize: Theme.typeSize("body_medium")
                    wrapMode: Text.WordWrap
                }

                Text {
                    width: parent.width
                    visible: page.result.warnings !== undefined
                        && page.result.warnings.length > 0
                    text: page.result.warnings === undefined ? ""
                        : "Замечания:\n• " + page.result.warnings.join("\n• ")
                    color: Theme.colors.warning
                    font.pixelSize: Theme.typeSize("body_medium")
                    wrapMode: Text.WordWrap
                }

                Text {
                    width: parent.width
                    visible: page.result.unsupported !== undefined
                        && page.result.unsupported.length > 0
                    text: page.result.unsupported === undefined ? ""
                        : "Без аналога в nfqws (не переносятся):\n• "
                          + page.result.unsupported.join("\n• ")
                    color: Theme.colors.warning
                    font.pixelSize: Theme.typeSize("body_medium")
                    wrapMode: Text.WordWrap
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                MButton {
                    id: applyButton
                    text: Backend.strategyBusy ? "Применение…" : "Сохранить и применить"
                    variant: "filled"
                    iconSource: "../icons/next.svg"
                    enabled: page.canApply && !Backend.strategyBusy
                    onClicked: page.applyNow()
                    KeyNavigation.up: convertButton
                    KeyNavigation.left: backButton
                }

                Text {
                    Layout.fillWidth: true
                    text: page.canApply
                        ? "Стратегия будет сохранена в files/strategy и применена"
                        : "Сначала преобразуйте строку — кнопка станет активной"
                    color: Theme.colors.on_surface_variant
                    font.pixelSize: Theme.typeSize("body_medium")
                    elide: Text.ElideRight
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    //: Подтверждение, когда часть опций BB DPI не переносится.
    Dialog {
        id: confirmDialog

        anchors.centerIn: parent
        width: Math.min(page.width - 2 * Theme.spaces.xl, 520)
        modal: true
        title: ""

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
                text: "Не всё перенесено"
                color: Theme.colors.on_surface
                font.pixelSize: Theme.typeSize("title_medium")
                font.weight: Font.Medium
            }

            Text {
                Layout.fillWidth: true
                text: page.result.unsupported === undefined ? ""
                    : "Опций без аналога в nfqws: " + page.result.unsupported.length
                      + ". Они не будут применены. Продолжить?"
                color: Theme.colors.on_surface_variant
                font.pixelSize: Theme.typeSize("body_medium")
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaces.sm

                Item { Layout.fillWidth: true }

                MButton {
                    text: "Нет"
                    variant: "text"
                    onClicked: confirmDialog.close()
                }

                MButton {
                    text: "Да"
                    variant: "filled"
                    onClicked: {
                        confirmDialog.close()
                        Backend.applyBbdpi(nameField.text, commandArea.text,
                                           hostlistCheck.checked)
                    }
                }
            }
        }
    }
}
