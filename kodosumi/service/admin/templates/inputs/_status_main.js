const fid = "{{ fid }}"; 
const eventSource = new EventSource(`/outputs/main/${fid}`);
let ioSource = null;
let sse_loaded = false;

elmToggleIcon.addEventListener('click', () => {
    elmDetailsElement.open = !elmDetailsElement.open;
    elmToggleIcon.textContent = elmDetailsElement.open ? 'arrow_drop_down' : 'arrow_right';
});

eventSource.onopen = function() {
    console.log("main SSE connection opened.");
};

eventSource.addEventListener('status', function(event) {
    const [ts, js] = parseData(event);
    applyToAll(elmStatus, (elm) => {elm.innerText = js});
    if (js === "finished" || js === "error") {
        active = false;
        eventSource.close();
        stopAutoSpark();
        elmProgress.value = 100;
        applyToAll(elmFinish, (elm) => {elm.innerText = formatUnixTime(ts)});
        redrawDynamicCharts();
        elmAbout.style.display = 'block';
        if (js === "error") {
            elmStatusValid.classList.add('error');
        }
        elmStatusIcon.innerText = "delete";
    }
});
eventSource.addEventListener('meta', function(event) {
    const [ts, body] = parseData(event);
    let js = JSON.parse(body);
    applyToAll(elmFID, (elm) => {elm.innerText = js.dict.fid});
    applyToAll(elmEntryPoint, (elm) => {elm.innerText = js.dict.entry_point});
    applyToAll(elmTags, (elm) => {elm.innerText = js.dict.tags});
    applyToAll(elmSummary, (elm) => {elm.innerText = js.dict.summary});
    applyToAll(elmDescription, (elm) => {elm.innerText = js.dict.description});
    applyToAll(elmAuthor, (elm) => {elm.innerText = js.dict.author});
    applyToAll(elmOrganization, (elm) => {elm.innerText = js.dict.organization});
    applyToAll(elmVersion, (elm) => {elm.innerText = js.dict.version});
    applyToAll(elmKodosumiVersion, (elm) => {elm.innerText = js.dict.kodosumi});
    
});
eventSource.addEventListener('inputs', function(event) {
    const [ts, body] = parseData(event);
    applyToAll(elmInputs, (elm) => {elm.innerText = body});
});
eventSource.addEventListener('result', function(event) {
    const [ts, js] = parseData(event);
    if (js != null) {
        elmOutput.innerHTML += js; 
        scrollDown();
    }
});
eventSource.addEventListener('final', function(event) {
    const [ts, js] = parseData(event);
    if (js != null) {
        elmFinal.innerHTML += js; 
        scrollDown();
        Array.prototype.forEach.call(elmFinalResult, (elm) => {elm.style.display = "block"});
    }
});
eventSource.addEventListener('error', function(event) {
    const [ts, js] = parseData(event);
    if (js != null) {
        elmFinal.innerHTML += '<pre><code class="error-text">' + js + '</code></pre>'; 
        scrollDown();
        Array.prototype.forEach.call(elmFinalResult, (elm) => {elm.style.display = "block"});
        scrollDown();
    }
});
eventSource.addEventListener('alive', function(event) {
    const [ts, js] = parseData(event, false);
    startAutoSpark();
});
eventSource.addEventListener('eof', function(event) {
    console.log('main Stream closed:', event);
    eventSource.close();
    stopAutoSpark();
});

function startStdioSSE() {
    if (ioSource == null) {
        startSTDIO();
    }
}

const stdio_observer = new MutationObserver((mutationsList, stdio_observer) => {
    for(const mutation of mutationsList) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
            const targetElement = mutation.target;
            if (targetElement.classList.contains('active')) {
                startStdioSSE();
            }
        }
    }
});
stdio_observer.observe(elmStdioPage, { attributes: true, attributeFilter: ['class'] });
if (elmStdioPage.classList.contains('active')) {
    startStdioSSE();
}

const event_observer = new MutationObserver((mutationsList, event_observer) => {
    console.log("event_observer", mutationsList);
    for(const mutation of mutationsList) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
            const targetElement = mutation.target;
            if (targetElement.classList.contains('active')) {
                startEventSSE();
                return;
            }
        }
    }
});
event_observer.observe(elmEventPage, { attributes: true, attributeFilter: ['class'] });
if (elmEventPage.classList.contains('active')) {
    startEventSSE();
}

window.addEventListener('resize', () => {
    const page = activePage().split("-")[1];
    const mainWidth = document.querySelector("#article-" + page).offsetWidth;
    elmArticleHead.style.width = `${mainWidth}px`;
    redrawDynamicCharts();
});

window.addEventListener('beforeunload', () => {
    if (eventSource && eventSource.readyState !== EventSource.CLOSED) {
        eventSource.close();
        stopAutoSpark();
    }
});
