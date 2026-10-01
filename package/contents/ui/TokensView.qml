pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras

// Token statistics: period and provider filters, a stacked daily chart
// and a ranked per-model list. Data contract: root.tokens in main.qml.
ColumnLayout {
    id: tokensView

    property var tokens: null
    property bool loading: false
    property string error: ""
    signal refreshRequested()

    // Filters live for the session only
    property int periodIndex: 1
    property int providerIndex: 0

    readonly property var periodKeys: ["1", "7", "30", "60", "90", "365"]
    readonly property string periodKey: periodKeys[periodIndex]
    readonly property bool showClaude: providerIndex !== 2
    readonly property bool showCodex: providerIndex !== 1
    readonly property bool stacked: showClaude && showCodex
    // "Today" shows the 7-day chart with today's bar emphasized
    readonly property bool emphasizeToday: periodIndex === 0

    // Series colors: the theme accent and its visited-link hue, which stay
    // apart under deutan and protan simulation (validated on Breeze).
    readonly property color claudeColor: Kirigami.Theme.highlightColor
    readonly property color codexColor: Kirigami.Theme.visitedLinkColor
    readonly property color otherColor: Kirigami.Theme.disabledTextColor

    readonly property bool hasData: !!tokens && !!tokens.daily && !!tokens.periods
    readonly property bool scanning: hasData && !!tokens.scanning
    readonly property var period: hasData ? (tokens.periods[periodKey] || null) : null
    readonly property real claudeTotal: period ? (Number(period.claude) || 0) : 0
    readonly property real codexTotal: period ? (Number(period.codex) || 0) : 0
    readonly property real total: (showClaude ? claudeTotal : 0) + (showCodex ? codexTotal : 0)
    // What the period would cost at API list prices; models without a known
    // price are left out and their tokens counted separately.
    readonly property var periodCost: period && period.cost ? period.cost : null
    readonly property var periodUnpriced: period && period.unpriced ? period.unpriced : null
    readonly property real claudeCost: periodCost ? (Number(periodCost.claude) || 0) : 0
    readonly property real codexCost: periodCost ? (Number(periodCost.codex) || 0) : 0
    readonly property real cost: (showClaude ? claudeCost : 0) + (showCodex ? codexCost : 0)
    readonly property real unpricedTokens: periodUnpriced ? (showClaude ? (Number(periodUnpriced.claude) || 0) : 0) + (showCodex ? (Number(periodUnpriced.codex) || 0) : 0) : 0
    readonly property bool hasCost: !!periodCost && cost > 0

    readonly property var days: {
        if (!hasData)
            return [];
        const all = Array.isArray(tokens.daily) ? tokens.daily.filter(d => !!d) : [];
        const n = [7, 7, 30, 60, 90, 365][periodIndex];
        const slice = all.slice(Math.max(0, all.length - n));
        // Long periods are grouped so the bars stay readable: weeks for 60
        // and 90 days, months for a year.
        if (periodIndex >= 5)
            return bucket(slice, "month");
        if (periodIndex >= 3)
            return bucket(slice, "week");
        return slice;
    }

    function bucket(list, unit) {
        const out = [];
        let cur = null;
        for (const d of list) {
            const date = parseDay(d.date);
            if (!date)
                continue;
            let key;
            if (unit === "month") {
                key = d.date.slice(0, 7) + "-01";
            } else {
                // Weeks start on the locale's first day of the week.
                const back = (date.getDay() - Qt.locale().firstDayOfWeek + 7) % 7;
                const start = new Date(date.getFullYear(), date.getMonth(), date.getDate() - back);
                key = Qt.formatDate(start, "yyyy-MM-dd");
            }
            if (!cur || cur.key !== key) {
                // date is the first day actually inside the period, so a
                // partial leading week or month is not labelled before it.
                cur = { key: key, date: d.date, unit: unit, claude: 0, codex: 0 };
                out.push(cur);
            }
            cur.claude += Number(d.claude) || 0;
            cur.codex += Number(d.codex) || 0;
        }
        return out;
    }
    readonly property real maxDay: days.reduce((m, d) => Math.max(m, dayValue(d)), 0)
    readonly property int maxIndex: {
        let best = -1;
        let bestValue = 0;
        for (let i = 0; i < days.length; i++) {
            const v = dayValue(days[i]);
            if (v > bestValue) {
                bestValue = v;
                best = i;
            }
        }
        return best;
    }
    readonly property real niceMax: niceCeil(maxDay)

    // Top models for the filter; the tail past eight folds into one row.
    readonly property var modelRows: {
        if (!period || !Array.isArray(period.models))
            return [];
        const rows = period.models.filter(m => !!m).filter(m => (m.provider === "claude" && showClaude) || (m.provider === "codex" && showCodex));
        const cap = 8;
        if (rows.length <= cap + 1)
            return rows;
        const head = rows.slice(0, cap);
        const tail = rows.slice(cap);
        const sum = key => tail.reduce((acc, m) => acc + (Number(m[key]) || 0), 0);
        const priced = tail.filter(m => typeof m.cost === "number");
        head.push({
            name: i18n("Other"),
            provider: "",
            folded: tail.length,
            total: sum("total"),
            input: sum("input"),
            output: sum("output"),
            cacheRead: sum("cacheRead"),
            cacheWrite: sum("cacheWrite"),
            cost: priced.length ? priced.reduce((acc, m) => acc + m.cost, 0) : null
        });
        return head;
    }
    readonly property real maxModel: modelRows.filter(r => !r.folded).reduce((m, r) => Math.max(m, Number(r.total) || 0), 0)

    spacing: Kirigami.Units.smallSpacing

    function dayValue(d) {
        return (showClaude ? (Number(d.claude) || 0) : 0) + (showCodex ? (Number(d.codex) || 0) : 0);
    }

    function niceCeil(v) {
        if (!(v > 0))
            return 1;
        const exp = Math.pow(10, Math.floor(Math.log10(v)));
        const f = v / exp;
        const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
        return nice * exp;
    }

    function seriesColor(provider) {
        if (provider === "claude")
            return claudeColor;
        if (provider === "codex")
            return codexColor;
        return otherColor;
    }

    function providerName(provider) {
        if (provider === "claude")
            return "Claude";
        if (provider === "codex")
            return "Codex";
        return "";
    }

    // 1.2B / 345M / 12.3K with the locale's decimal separator
    function compact(n) {
        n = Number(n) || 0;
        const abs = Math.abs(n);
        const locale = Qt.locale();
        const scaled = (unit) => {
            const v = Math.round(n / unit * 10) / 10;
            return locale.toString(v, 'f', Math.abs(v) < 10 ? 1 : 0);
        };
        if (abs >= 999.5e6)
            return i18nc("billion tokens, compact", "%1B", scaled(1e9));
        if (abs >= 999.5e3)
            return i18nc("million tokens, compact", "%1M", scaled(1e6));
        if (abs >= 999.5)
            return i18nc("thousand tokens, compact", "%1K", scaled(1e3));
        return locale.toString(Math.round(n), 'f', 0);
    }

    // Attached tooltips and placeholder explanations have no textFormat hook,
    // so keep markup from local log values out of them.
    function plain(s) {
        return String(s === undefined || s === null ? "" : s).replace(/[<>]/g, "");
    }

    // Always US dollars, whatever the locale's currency: $1,234 / $12.34
    function money(v) {
        v = Number(v) || 0;
        const digits = v >= 100 ? 0 : 2;
        return i18nc("amount in US dollars", "$%1", Qt.locale().toString(v, 'f', digits));
    }

    function hasModelCost(m) {
        return typeof m.cost === "number" && isFinite(m.cost);
    }

    function exact(n) {
        return Qt.locale().toString(Math.round(Number(n) || 0), 'f', 0);
    }

    // "2026-10-01" is a local day; build the Date in local time
    function parseDay(iso) {
        const parts = String(iso || "").split("-");
        if (parts.length !== 3)
            return null;
        return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    }

    function dateLabel(iso, unit) {
        const d = parseDay(iso);
        if (!d)
            return String(iso || "");
        return Qt.locale().toString(d, unit === "month" ? "MMM" : "d MMM");
    }

    function dayTooltip(d) {
        const claude = Number(d.claude) || 0;
        const codex = Number(d.codex) || 0;
        const date = parseDay(d.date);
        let title = dateLabel(d.date);
        if (date && d.unit === "month")
            title = Qt.locale().toString(date, "MMMM yyyy");
        else if (date && d.unit === "week")
            title = i18nc("bar tooltip title, %1 is the first day of the week", "Week of %1", dateLabel(d.date));
        const lines = [title];
        if (stacked)
            lines.push(i18n("Total: %1", exact(claude + codex)));
        if (showClaude)
            lines.push(i18n("Claude: %1", exact(claude)));
        if (showCodex)
            lines.push(i18n("Codex: %1", exact(codex)));
        return lines.join("\n");
    }

    function modelTooltip(m) {
        return [
            plain(m.name),
            i18n("Total: %1", exact(m.total)),
            i18n("Input: %1", exact(m.input)),
            i18n("Output: %1", exact(m.output)),
            i18n("Cache read: %1", exact(m.cacheRead)),
            i18n("Cache write: %1", exact(m.cacheWrite)),
            hasModelCost(m) ? i18n("At API prices: %1", money(m.cost)) : i18n("At API prices: no known price")
        ].join("\n");
    }

    // First scan still running
    PlasmaExtras.PlaceholderMessage {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.gridUnit * 2
        Layout.bottomMargin: Kirigami.Units.gridUnit * 2
        visible: !tokensView.hasData && tokensView.loading
        text: i18n("Reading token logs…")

        PlasmaComponents3.BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: parent.visible
        }
    }

    // Nothing cached and the scan failed
    PlasmaExtras.PlaceholderMessage {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.gridUnit * 2
        Layout.bottomMargin: Kirigami.Units.gridUnit * 2
        visible: !tokensView.hasData && !tokensView.loading
        iconName: tokensView.error ? "data-error" : "view-statistics"
        text: tokensView.error ? i18n("Could not read token logs") : i18n("No data yet")
        explanation: tokensView.plain(tokensView.error)
        helpfulAction: QQC2.Action {
            icon.name: "view-refresh"
            text: i18n("Try again")
            onTriggered: tokensView.refreshRequested()
        }
    }

    ColumnLayout {
        id: content

        Layout.fillWidth: true
        visible: tokensView.hasData
        spacing: Kirigami.Units.smallSpacing
        // Refetch keeps the previous render, only dimmed
        opacity: tokensView.loading ? 0.6 : 1

        Behavior on opacity {
            NumberAnimation {
                duration: Kirigami.Units.shortDuration
            }
        }

        // Filter row: period first, then provider
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents3.ComboBox {
                Layout.fillWidth: true
                model: [i18n("Today"), i18n("7 days"), i18n("30 days"), i18n("60 days"), i18n("90 days"), i18n("1 year")]
                currentIndex: tokensView.periodIndex
                onActivated: index => tokensView.periodIndex = index
            }

            PlasmaComponents3.ComboBox {
                Layout.fillWidth: true
                model: [i18n("All"), "Claude", "Codex"]
                currentIndex: tokensView.providerIndex
                onActivated: index => tokensView.providerIndex = index
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: tokensView.scanning
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                Layout.preferredWidth: Kirigami.Units.iconSizes.small
                Layout.preferredHeight: Kirigami.Units.iconSizes.small
                source: "data-information"
                color: Kirigami.Theme.disabledTextColor
            }

            PlasmaComponents3.Label {
                Layout.fillWidth: true
                text: i18n("First scan in progress, numbers may be incomplete")
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
                wrapMode: Text.Wrap
            }
        }

        // Hero figure for the period
        PlasmaExtras.Heading {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.smallSpacing
            level: 1
            text: tokensView.compact(tokensView.total)
            elide: Text.ElideRight
        }

        PlasmaComponents3.Label {
            Layout.fillWidth: true
            text: {
                switch (tokensView.periodIndex) {
                case 0:
                    return i18n("Total tokens today");
                case 2:
                    return i18n("Total tokens, last 30 days");
                case 3:
                    return i18n("Total tokens, last 60 days");
                case 4:
                    return i18n("Total tokens, last 90 days");
                case 5:
                    return i18n("Total tokens, last year");
                default:
                    return i18n("Total tokens, last 7 days");
                }
            }
            color: Kirigami.Theme.disabledTextColor
            font: Kirigami.Theme.smallFont
            elide: Text.ElideRight
        }

        // Pay-as-you-go equivalent of the same tokens
        PlasmaComponents3.Label {
            id: costLabel

            Layout.fillWidth: true
            visible: tokensView.hasCost
            text: i18n("≈ %1 at API prices, cache included", tokensView.money(tokensView.cost))
            elide: Text.ElideRight

            HoverHandler {
                id: costHover
            }

            PlasmaComponents3.ToolTip.text: i18n("What these tokens would cost on the pay-as-you-go API, cache reads and writes included. Standard list prices from %1; long-context surcharges are not applied.", tokensView.plain(tokensView.tokens && tokensView.tokens.pricesAsOf))
            PlasmaComponents3.ToolTip.visible: costHover.hovered
            PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
        }

        PlasmaComponents3.Label {
            Layout.fillWidth: true
            visible: tokensView.hasCost && tokensView.unpricedTokens > 0
            text: i18n("Not included: %1 tokens from models with no known price", tokensView.compact(tokensView.unpricedTokens))
            color: Kirigami.Theme.disabledTextColor
            font: Kirigami.Theme.smallFont
            wrapMode: Text.Wrap
        }

        // Legend doubles as the per-series breakdown
        RowLayout {
            Layout.fillWidth: true
            visible: tokensView.stacked
            spacing: Kirigami.Units.largeSpacing

            Repeater {
                model: [
                    { name: "Claude", value: tokensView.claudeTotal, cost: tokensView.claudeCost, color: tokensView.claudeColor },
                    { name: "Codex", value: tokensView.codexTotal, cost: tokensView.codexCost, color: tokensView.codexColor }
                ]

                delegate: RowLayout {
                    id: legendItem
                    required property var modelData
                    spacing: Kirigami.Units.smallSpacing

                    Rectangle {
                        implicitWidth: Kirigami.Units.smallSpacing * 2
                        implicitHeight: implicitWidth
                        radius: Math.round(Kirigami.Units.smallSpacing / 2)
                        color: legendItem.modelData.color
                    }

                    PlasmaComponents3.Label {
                        text: legendItem.modelData.name
                        font: Kirigami.Theme.smallFont
                    }

                    PlasmaComponents3.Label {
                        text: tokensView.compact(legendItem.modelData.value)
                        font.bold: true
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                    }

                    PlasmaComponents3.Label {
                        visible: tokensView.hasCost && legendItem.modelData.cost > 0
                        text: tokensView.money(legendItem.modelData.cost)
                        color: Kirigami.Theme.disabledTextColor
                        font: Kirigami.Theme.smallFont
                    }
                }
            }
        }

        Kirigami.Separator {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.smallSpacing
            Layout.bottomMargin: Kirigami.Units.smallSpacing
        }

        PlasmaExtras.Heading {
            level: 3
            text: (tokensView.periodIndex >= 5 ? i18n("Monthly") : tokensView.periodIndex >= 3 ? i18nc("chart title, tokens per week", "Weekly") : i18n("Daily"))
        }

        // Stacked column chart drawn with plain Rectangles
        Item {
            id: chart

            readonly property real topPad: Kirigami.Units.gridUnit
            readonly property real plotHeight: Kirigami.Units.gridUnit * 6
            readonly property real axisBand: Kirigami.Units.gridUnit
            readonly property real baseY: topPad + plotHeight
            readonly property real gutter: Math.max(topTick.implicitWidth, midTick.implicitWidth) + Kirigami.Units.smallSpacing
            readonly property real plotWidth: Math.max(0, width - gutter)
            readonly property int count: tokensView.days.length
            readonly property real slotWidth: count > 0 ? plotWidth / count : 0
            // Surface gap between touching marks, and the rounded data-end
            readonly property real gap: Math.max(1, Math.round(Kirigami.Units.smallSpacing / 2))
            readonly property real cornerRadius: Kirigami.Units.smallSpacing
            readonly property real barWidth: Math.max(2, Math.min(Kirigami.Units.iconSizes.smallMedium, slotWidth - gap))
            readonly property int labelIndex: tokensView.emphasizeToday ? count - 1 : tokensView.maxIndex
            readonly property bool emptyPeriod: tokensView.maxDay <= 0
            // Label every slot when it is wide enough, otherwise every other
            // one; the 30-day daily view labels one day per week.
            readonly property bool grouped: count > 0 && !!tokensView.days[0].unit
            readonly property int labelStride: (count > 7 && !grouped) ? 7 : (slotWidth >= Kirigami.Units.gridUnit * 2.4 ? 1 : 2)

            function barHeight(v) {
                const usable = plotHeight - (tokensView.stacked ? gap : 0);
                return v > 0 ? Math.max(1, usable * v / tokensView.niceMax) : 0;
            }

            function stackHeight(d) {
                const c = tokensView.showClaude ? barHeight(Number(d.claude) || 0) : 0;
                const x = tokensView.showCodex ? barHeight(Number(d.codex) || 0) : 0;
                return c + x + (c > 0 && x > 0 ? gap : 0);
            }

            Layout.fillWidth: true
            implicitHeight: topPad + plotHeight + axisBand

            // Hairline grid: top tick, mid tick, baseline
            Rectangle {
                x: chart.gutter
                y: chart.topPad
                width: chart.plotWidth
                height: 1
                color: Qt.alpha(Kirigami.Theme.textColor, 0.12)
            }

            Rectangle {
                x: chart.gutter
                y: chart.topPad + chart.plotHeight / 2
                width: chart.plotWidth
                height: 1
                color: Qt.alpha(Kirigami.Theme.textColor, 0.12)
            }

            Rectangle {
                x: chart.gutter
                y: chart.baseY
                width: chart.plotWidth
                height: 1
                color: Qt.alpha(Kirigami.Theme.textColor, 0.3)
            }

            PlasmaComponents3.Label {
                id: topTick
                visible: !chart.emptyPeriod
                x: 0
                y: chart.topPad - height / 2
                text: tokensView.compact(tokensView.niceMax)
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
            }

            PlasmaComponents3.Label {
                id: midTick
                visible: !chart.emptyPeriod
                x: 0
                y: chart.topPad + chart.plotHeight / 2 - height / 2
                text: tokensView.compact(tokensView.niceMax / 2)
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
            }

            PlasmaComponents3.Label {
                x: chart.gutter + (chart.plotWidth - width) / 2
                y: chart.topPad + (chart.plotHeight - height) / 2
                visible: chart.emptyPeriod
                text: i18n("No usage in this period yet")
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
            }

            Repeater {
                model: tokensView.days

                // One slot per day; the whole slot is the hover target
                delegate: Item {
                    id: slot

                    required property var modelData
                    required property int index
                    readonly property real claude: tokensView.showClaude ? (Number(modelData.claude) || 0) : 0
                    readonly property real codex: tokensView.showCodex ? (Number(modelData.codex) || 0) : 0
                    readonly property real claudeH: chart.barHeight(claude)
                    readonly property real codexH: chart.barHeight(codex)
                    readonly property bool dimmed: tokensView.emphasizeToday && index !== chart.count - 1 && !slotHover.hovered

                    x: chart.gutter + index * chart.slotWidth
                    y: chart.topPad
                    width: chart.slotWidth
                    height: chart.plotHeight
                    opacity: dimmed ? 0.4 : 1

                    Behavior on opacity {
                        NumberAnimation {
                            duration: Kirigami.Units.shortDuration
                        }
                    }

                    HoverHandler {
                        id: slotHover
                    }

                    PlasmaComponents3.ToolTip.text: tokensView.dayTooltip(slot.modelData)
                    PlasmaComponents3.ToolTip.visible: slotHover.hovered
                    PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay

                    Rectangle {
                        anchors.fill: parent
                        visible: slotHover.hovered
                        radius: chart.cornerRadius
                        color: Qt.alpha(Kirigami.Theme.textColor, 0.06)
                    }

                    // Claude segment sits on the baseline
                    Rectangle {
                        id: claudeBar
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        width: chart.barWidth
                        height: slot.claudeH
                        visible: height > 0
                        color: slotHover.hovered ? Qt.lighter(tokensView.claudeColor, 1.15) : tokensView.claudeColor
                        topLeftRadius: slot.codexH > 0 ? 0 : chart.cornerRadius
                        topRightRadius: topLeftRadius

                        Behavior on height {
                            NumberAnimation {
                                duration: Kirigami.Units.longDuration
                                easing.type: Easing.OutCubic
                            }
                        }
                    }

                    // Codex segment stacks on the Claude bar with a surface gap,
                    // following it while it animates
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: claudeBar.visible ? claudeBar.top : parent.bottom
                        anchors.bottomMargin: claudeBar.visible ? chart.gap : 0
                        width: chart.barWidth
                        height: slot.codexH
                        visible: height > 0
                        color: slotHover.hovered ? Qt.lighter(tokensView.codexColor, 1.15) : tokensView.codexColor
                        topLeftRadius: chart.cornerRadius
                        topRightRadius: chart.cornerRadius

                        Behavior on height {
                            NumberAnimation {
                                duration: Kirigami.Units.longDuration
                                easing.type: Easing.OutCubic
                            }
                        }
                    }
                }
            }

            // One direct label: the peak day, or today in the "Today" view
            PlasmaComponents3.Label {
                id: peakLabel

                readonly property var day: (chart.labelIndex >= 0 && chart.labelIndex < chart.count) ? tokensView.days[chart.labelIndex] : null
                readonly property real value: day ? tokensView.dayValue(day) : 0
                readonly property real barTop: day ? chart.baseY - chart.stackHeight(day) : chart.baseY

                visible: value > 0
                x: Math.max(chart.gutter, Math.min(chart.width - width, chart.gutter + (chart.labelIndex + 0.5) * chart.slotWidth - width / 2))
                y: Math.max(0, barTop - height - chart.gap)
                text: tokensView.compact(value)
                font: Kirigami.Theme.smallFont
            }

            // X axis: dates in the locale's short form
            Repeater {
                model: tokensView.days

                delegate: PlasmaComponents3.Label {
                    id: axisLabel

                    required property var modelData
                    required property int index

                    visible: (chart.count - 1 - index) % chart.labelStride === 0
                    x: Math.max(0, Math.min(chart.width - width, chart.gutter + (index + 0.5) * chart.slotWidth - width / 2))
                    y: chart.baseY + Kirigami.Units.smallSpacing
                    text: tokensView.dateLabel(modelData.date, modelData.unit)
                    color: Kirigami.Theme.disabledTextColor
                    font: Kirigami.Theme.smallFont
                }
            }
        }

        Kirigami.Separator {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.smallSpacing
            Layout.bottomMargin: Kirigami.Units.smallSpacing
        }

        PlasmaExtras.Heading {
            level: 3
            text: i18n("By model")
        }

        PlasmaComponents3.Label {
            Layout.fillWidth: true
            visible: tokensView.modelRows.length === 0
            text: i18n("No usage in this period yet")
            color: Kirigami.Theme.disabledTextColor
            wrapMode: Text.Wrap
        }

        // Ranked list: name, provider, compact total and a horizontal bar
        Repeater {
            model: tokensView.modelRows

            delegate: ColumnLayout {
                id: modelRow

                required property var modelData
                readonly property real total: Number(modelData.total) || 0
                readonly property color tone: tokensView.seriesColor(modelData.provider)
                readonly property string providerLabel: modelData.folded ? i18np("%1 model", "%1 models", modelData.folded) : tokensView.providerName(modelData.provider)

                Layout.fillWidth: true
                Layout.topMargin: Math.round(Kirigami.Units.smallSpacing / 2)
                spacing: Math.round(Kirigami.Units.smallSpacing / 2)

                HoverHandler {
                    id: modelHover
                }

                PlasmaComponents3.ToolTip.text: tokensView.modelTooltip(modelRow.modelData)
                PlasmaComponents3.ToolTip.visible: modelHover.hovered
                PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    Rectangle {
                        implicitWidth: Kirigami.Units.smallSpacing * 2
                        implicitHeight: implicitWidth
                        radius: Math.round(Kirigami.Units.smallSpacing / 2)
                        color: modelRow.tone
                    }

                    PlasmaComponents3.Label {
                        Layout.fillWidth: true
                        text: modelRow.modelData.name || ""
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                    }

                    PlasmaComponents3.Label {
                        visible: text.length > 0
                        text: modelRow.providerLabel
                        color: Kirigami.Theme.disabledTextColor
                        font: Kirigami.Theme.smallFont
                    }

                    PlasmaComponents3.Label {
                        visible: tokensView.hasModelCost(modelRow.modelData)
                        text: visible ? tokensView.money(modelRow.modelData.cost) : ""
                        color: Kirigami.Theme.disabledTextColor
                        font: Kirigami.Theme.smallFont
                    }

                    PlasmaComponents3.Label {
                        text: tokensView.compact(modelRow.total)
                        font.bold: true
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.round(Kirigami.Units.smallSpacing * 1.5)
                    radius: height / 2
                    color: Qt.alpha(Kirigami.Theme.textColor, 0.1)

                    Rectangle {
                        anchors {
                            left: parent.left
                            top: parent.top
                            bottom: parent.bottom
                        }
                        width: tokensView.maxModel > 0 ? Math.max(height, Math.min(parent.width, parent.width * modelRow.total / tokensView.maxModel)) : 0
                        radius: parent.radius
                        color: modelHover.hovered ? Qt.lighter(modelRow.tone, 1.15) : modelRow.tone

                        Behavior on width {
                            NumberAnimation {
                                duration: Kirigami.Units.longDuration
                                easing.type: Easing.OutCubic
                            }
                        }
                    }
                }
            }
        }
    }
}
