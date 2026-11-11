import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Модалка пароля sudo: поле, галочка «Запомнить» и проверка — в одном окне.
// Показывается мостом (Backend.sudoPasswordRequested) по заявке из ядра;
// без галочки пароль живёт только до конца текущего действия.
Dialog {
    id: root

    objectName: "sudoPasswordDialog"
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    padding: Theme.spaces.lg
    width: Math.min((parent ? parent.width : 1180) - 2 * Theme.spaces.xl, 520)
    anchors.centerIn: parent

    readonly property bool canSubmit: passwordField.text.length > 0
        && !Backend.sudoPasswordBusy

    visible: Backend.sudoPasswordRequested

    function submit() {
        if (!canSubmit) {
            return
        }
        Backend.submitSudoPassword(passwordField.text, rememberBox.checked)
    }

    background: Rectangle {
        color: Theme.colors.surface_container
        radius: Theme.radii.lg
        border.width: 1
        border.color: Theme.colors.outline_variant
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaces.md

        Keys.onEscapePressed: Backend.cancelSudoPassword()

        Text {
            Layout.fillWidth: true
            text: "Требуется пароль sudo"
            color: Theme.colors.on_surface
            font.pixelSize: Theme.typeSize("title_medium")
            font.weight: Font.Medium
        }

        Text {
            Layout.fillWidth: true
            text: "Для этого действия нужны права администратора. Без галочки "
                + "пароль спросят снова при следующем действии."
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_medium")
            wrapMode: Text.WordWrap
        }

        TextField {
            id: passwordField

            objectName: "sudoPasswordField"
            Layout.fillWidth: true
            placeholderText: "Пароль"
            echoMode: TextInput.Password
            selectByMouse: true
            enabled: !Backend.sudoPasswordBusy
            color: Theme.colors.on_surface
            placeholderTextColor: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_large")
            onAccepted: root.submit()
            KeyNavigation.down: rememberBox
            background: Rectangle {
                radius: Theme.radii.sm
                color: Theme.colors.surface_container_high
                border.width: 1
                border.color: passwordField.activeFocus ? Theme.colors.primary
                    : Theme.colors.outline_variant
            }
        }

        MCheckBox {
            id: rememberBox

            objectName: "sudoRememberCheck"
            Layout.fillWidth: true
            text: "Запомнить пароль"
            checked: false
            enabled: !Backend.sudoPasswordBusy
            KeyNavigation.up: passwordField
            KeyNavigation.down: cancelButton
        }

        Text {
            Layout.fillWidth: true
            visible: Backend.sudoPasswordError.length > 0
            text: Backend.sudoPasswordError
            color: Theme.colors.error
            font.pixelSize: Theme.typeSize("body_medium")
            wrapMode: Text.WordWrap
        }

        Text {
            Layout.fillWidth: true
            visible: Backend.sudoPasswordBusy
            text: "Проверка пароля…"
            color: Theme.colors.on_surface_variant
            font.pixelSize: Theme.typeSize("body_small")
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaces.sm

            Item { Layout.fillWidth: true }

            MButton {
                id: cancelButton
                objectName: "sudoCancelButton"
                text: "Отмена"
                variant: "text"
                enabled: !Backend.sudoPasswordBusy
                onClicked: Backend.cancelSudoPassword()
                KeyNavigation.up: rememberBox
                KeyNavigation.right: submitButton
            }

            MButton {
                id: submitButton
                objectName: "sudoSubmitButton"
                text: "Подтвердить"
                variant: "filled"
                enabled: root.canSubmit
                onClicked: root.submit()
                KeyNavigation.up: rememberBox
                KeyNavigation.left: cancelButton
            }
        }
    }

    // Ошибка проверки: выделяем введённое, чтобы сразу заменить пароль.
    Connections {
        target: Backend

        function onSudoPasswordChanged() {
            if (Backend.sudoPasswordRequested && Backend.sudoPasswordError.length > 0
                    && !Backend.sudoPasswordBusy) {
                passwordField.select(0, passwordField.text.length)
                passwordField.forceActiveFocus()
            }
        }
    }

    onOpened: {
        passwordField.text = ""
        rememberBox.checked = false
        passwordField.forceActiveFocus()
    }
}
