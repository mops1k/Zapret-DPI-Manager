import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"
import "pages"

// Главное окно Zapret DPI Manager на Qt Quick.
ApplicationWindow {
    id: root

    visible: true
    width: 1180
    height: 720
    minimumWidth: 720
    minimumHeight: 480
    title: "Zapret DPI Manager"
    color: Theme.colors.surface

    //: Глубина стека экранов (для самопроверки).
    readonly property int pageDepth: stack.depth

    // Окно не должно вылезать за экран: подгоняем после создания окна,
    // когда привязанный Screen уже известен (на Steam Deck остаётся 1180x720).
    Component.onCompleted: {
        if (Screen.width > 0) {
            width = Math.min(width, Screen.width - 96)
            height = Math.min(height, Screen.height - 96)
        }
    }

    StackView {
        id: stack
        objectName: "stack"
        anchors.fill: parent

        initialItem: MainPage {
            id: mainPage
            onOpenStrategy: stack.push(strategyComponent)
            onOpenWarp: stack.push(warpComponent)
            onOpenHostlist: stack.push(hostlistComponent)
            onOpenIpset: stack.push(ipsetComponent)
            onOpenConnectionCheck: stack.push(checkComponent)
            onOpenDns: stack.push(dnsComponent)
            onOpenGameFilter: stack.push(gameFilterComponent)
            onOpenUpdate: stack.push(updateComponent)
            onOpenUnlock: stack.push(unlockComponent)
            onOpenInfo: stack.push(infoComponent)
            onOpenReport: stack.push(reportComponent)
        }

        onCurrentItemChanged: if (currentItem === mainPage && mainPage.leftOnce)
            Qt.callLater(mainPage.restoreFocus)

    }

    //: Открыть экран стратегий из Python (отладка, скриншоты).
    function openStrategyPage() {
        if (stack.depth === 1) {
            mainPage.leaveToStrategy()
        }
    }

    //: Открыть экран автоподбора из Python (отладка, скриншоты).
    function openTesterPage() {
        if (stack.depth === 1) {
            stack.push(testerComponent)
        }
    }

    //: Открыть экран проверки соединения из Python (отладка, скриншоты).
    function openCheckPage() {
        if (stack.depth === 1) {
            mainPage.openConnectionCheck()
        }
    }

    //: Открыть экраны обновления/разблокировки/информации/отчёта (отладка, скриншоты).
    function openUpdatePage() {
        if (stack.depth === 1) {
            mainPage.openUpdate()
        }
    }

    function openUnlockPage() {
        if (stack.depth === 1) {
            mainPage.openUnlock()
        }
    }

    function openInfoPage() {
        if (stack.depth === 1) {
            mainPage.openInfo()
        }
    }

    function openReportPage() {
        if (stack.depth === 1) {
            mainPage.openReport()
        }
    }

    //: Открыть экран DNS из Python (отладка, скриншоты).
    function openDnsPage() {
        if (stack.depth === 1) {
            mainPage.openDns()
        }
    }

    //: Открыть экран GameFilter из Python (отладка, скриншоты).
    function openGameFilterPage() {
        if (stack.depth === 1) {
            mainPage.openGameFilter()
        }
    }

    //: Открыть экран IPSet из Python (отладка, скриншоты).
    function openIpsetPage() {
        if (stack.depth === 1) {
            mainPage.openIpset()
        }
    }

    //: Открыть экран Hostlist из Python (отладка, скриншоты).
    function openHostlistPage() {
        if (stack.depth === 1) {
            mainPage.openHostlist()
        }
    }

    //: Открыть экран WARP из Python (отладка, скриншоты).
    function openWarpPage() {
        if (stack.depth === 1) {
            mainPage.leaveToWarp()
        }
    }

    Component {
        id: strategyComponent

        StrategyPage {
            onBack: stack.pop()
            onOpenCustom: stack.push(customComponent)
            onOpenTester: stack.push(testerComponent)
            onOpenBbdpi: stack.push(bbdpiComponent)
        }
    }

    Component {
        id: testerComponent

        TesterPage {
            onBack: stack.pop()
            onOpenReport: stack.push(reportComponent)
        }
    }

    Component {
        id: customComponent

        CustomStrategyPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: checkComponent

        ConnectionCheckPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: updateComponent

        UpdatePage {
            onBack: stack.pop()
        }
    }

    Component {
        id: unlockComponent

        UnlockPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: infoComponent

        InfoPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: reportComponent

        ReportPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: dnsComponent

        DnsPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: gameFilterComponent

        GameFilterPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: ipsetComponent

        IpsetPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: hostlistComponent

        HostlistPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: warpComponent

        WarpPage {
            onBack: stack.pop()
        }
    }

    Component {
        id: bbdpiComponent

        BbdpiPage {
            onBack: stack.pop()
        }
    }

    // --- Статусная строка (общая для всех экранов) --------------------------

    Snackbar {
        id: snackbar
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spaces.lg
        width: Math.min(root.width - 2 * Theme.spaces.xl, 900)
    }

    Connections {
        target: Backend

        function onMessageChanged() {
            snackbar.show(Backend.message, Backend.messageKind)
        }
    }
}
