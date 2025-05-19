function drawSparklineBarChart(targetSelector, data) {
    const container = d3.select(targetSelector);
    const node = container.node();
    if (!node) {
        return;
    }
    const rect = node.getBoundingClientRect();
    let parentWidth = rect.width;
    let parentHeight = rect.height;
    if (parentWidth <= 0 || parentHeight <= 0) {
        const computedStyle = window.getComputedStyle(node);
        parentWidth = parseFloat(computedStyle.width) || 100;
        parentHeight = parseFloat(computedStyle.height) || 20;
        if (parentWidth <= 0 || parentHeight <= 0) {
            return;
        }
    }
    const svgWidth = parentWidth * 0.9; 
    const svgHeight = parentHeight * 1.0;
    if (!data || data.length === 0) {
        return;
    }
    const marginTop = 1;
    const marginRight = 1;
    const marginBottom = 1;
    const marginLeft = 1;
    const x = d3.scaleBand()
        .domain(d3.range(data.length))
        .range([marginLeft, svgWidth - marginRight])
        .padding(0.1);
    const y = d3.scaleLinear()
        .domain(d3.extent([0, ...data]))
        .range([svgHeight - marginBottom, marginTop]);
    let svg = container.select("svg");
    if (svg.empty()) {
        svg = container.append("svg");
        svg.append("g").attr("class", "bars");
    }
    svg.attr("width", svgWidth)
        .attr("height", svgHeight)
        .attr("viewBox", [0, 0, svgWidth, svgHeight])
        .style("display", "block")
        .style("margin-left", "auto")
        .style("margin-right", "auto");
    const yZero = y(0);
    svg.select("g.bars").selectAll("*").remove(); 
    svg.select("g.bars")
        .selectAll("rect.sparkline-bar")
        .data(data)
        .join(
            enter => enter.append("rect")
                .attr("class", d => "sparkline-bar" + (d < 0 ? " negative" : ""))
                .attr("x", (d, i) => x(i))
                .attr("y", d => (d >= 0 ? y(d) : yZero))
                .attr("height", d => Math.abs(y(d) - yZero))
                .attr("width", x.bandwidth()),
            update => update
                .attr("class", d => "sparkline-bar" + (d < 0 ? " negative" : ""))
                .attr("x", (d, i) => x(i))
                .attr("y", d => (d >= 0 ? y(d) : yZero))
                .attr("height", d => Math.abs(y(d) - yZero))
                .attr("width", x.bandwidth()),
            exit => exit.remove()
        );
}

function redrawDynamicCharts() {
    if (eventActivity.size === 0) {
        drawSparklineBarChart("#sparkline", []); 
        return;
    }
    const latestTimestamp = Math.max(...eventActivity.keys());
    const startTimestamp = latestTimestamp - (windowSize - 1);
    const chartData = [];
    for (let ts = startTimestamp; ts <= latestTimestamp; ts++) {
        chartData.push(eventActivity.get(ts) || 0); 
    }
    drawSparklineBarChart("#sparkline", chartData);
}

function secondsToHHMMSS(seconds) {
    seconds = Math.round(seconds);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function formatUnixTime(unixTime) {
    const date = new Date(unixTime * 1000);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function applyToAll(els, func) {
    Array.prototype.forEach.call(els, func);
}

function splitData(event) {
    if (event.lastEventId > 0) {
        const parts = event.data.split(":");
        const ts = parts[0];
        let js = parts.slice(1).join(":");
        return [event.lastEventId, ts, js];
    } else {
        return [null, null, null];
    }
}


function activePage() {
    for (const page of [elmStdioPage, elmEventPage, elmOutputPage]) {
        if (page.classList.contains("active")) {
            return page.id;
        }
    }
    return null;
}
