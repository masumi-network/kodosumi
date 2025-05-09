const createCircleConfigs = (percent) => {
    return {
        svg: {
            viewBox: "0 0 34 34",
            width: "30",
            height: "30"
        },
        background: {
            cx: "17",
            cy: "17",
            r: "8",
            fill: "transparent",
            stroke: "var(--secondary-container)",
            "stroke-width": "5px"
        },
        progress: {
            cx: "17",
            cy: "17",
            r: "8",
            fill: "transparent",
            stroke: "var(--secondary)",
            "stroke-width": "2px",
            pathLength: "100",
            "stroke-dasharray": `${percent} ${100 - percent}`,
            "stroke-dashoffset": "-75"
        },
        spinner: {
            cx: "17",
            cy: "17",
            r: "8",
            fill: "transparent",
            stroke: "var(--secondary)",
            "stroke-width": "2px",
            "stroke-dasharray": "12 6", 
            class: "rotating-circle"
        }
    };
};

const setAttributes = (element, attributes) => {
    for (const [key, value] of Object.entries(attributes)) {
        element.setAttribute(key, value);
    }
};

function create_progress_circle(svg) {
    svg.innerHTML = '';
    const percent = svg.getAttribute('data') || 70;
    const circleConfigs = createCircleConfigs(percent);
    setAttributes(svg, circleConfigs.svg);
    // background
    const bgCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    setAttributes(bgCircle, circleConfigs.background);
    // progress
    const progressCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    setAttributes(progressCircle, circleConfigs.progress);
    svg.appendChild(bgCircle);
    svg.appendChild(progressCircle);
}

function create_empty_circle(svg) {
    svg.innerHTML = '';
    const circleConfigs = createCircleConfigs(0);
    setAttributes(svg, circleConfigs.svg);
}

function create_spinner_circle(svg) {
    svg.innerHTML = '';
    const circleConfigs = createCircleConfigs(0);
    setAttributes(svg, circleConfigs.svg);
    // background
    const bgCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    setAttributes(bgCircle, circleConfigs.background);
    // spinner
    const spinnerCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    setAttributes(spinnerCircle, circleConfigs.spinner);
    svg.appendChild(bgCircle);
    svg.appendChild(spinnerCircle);
}

document.querySelectorAll('.progress-circle').forEach(svg => {
    create_progress_circle(svg);
});
document.querySelectorAll('.empty-circle').forEach(svg => {
    create_empty_circle(svg);
});
document.querySelectorAll('.spinner-circle').forEach(svg => {
    create_spinner_circle(svg);
});
