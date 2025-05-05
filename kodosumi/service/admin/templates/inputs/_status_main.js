const fid = "{{ fid }}"; 
const eventSource = new EventSource(`/outputs/main/${fid}`);

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
    // console.log("have inputs", body);
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
    // console.log('Stream error:', event);
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
window.addEventListener('beforeunload', () => {
    if (eventSource && eventSource.readyState !== EventSource.CLOSED) {
        eventSource.close();
        stopAutoSpark();
    }
});
window.addEventListener('resize', () => {
    const page = activePage().split("-")[1];
    const mainWidth = document.querySelector("#article-" + page).offsetWidth;
    // console.log("active page", page, "width", mainWidth);
    elmArticleHead.style.width = `${mainWidth}px`;
    redrawDynamicCharts();
});

let ioSource = null;

function startSTDIO() {

    ioSource = new EventSource(`/outputs/stdio/${fid}`);
    ioSource.onopen = function() {
        console.log("stdio SSE connection opened.");
    };
    ioSource.addEventListener('stdout', function(event) {
        const [id, ts, js] = splitData(event);
        if (js != null) {
            elmStdio.innerHTML += '<span class="primary-text">' +  js + "<br/>";
            scrollDown();
        }
    });
    ioSource.addEventListener('stderr', function(event) {
        const [id, ts, js] = splitData(event);
        if (js != null) {
            elmStdio.innerHTML += '<span class="error-text">' +  js + "<br/>";
            scrollDown();
        }
    });
    ioSource.addEventListener('error', function(event) {
        const [id, ts, js] = splitData(event);
        if (js != null) {
            elmStdio.innerHTML += '<span class="error-text">' +  js + "<br/>";
            scrollDown();
        }
    });
    ioSource.addEventListener('eof', function(event) {
        ioSource.close();
    });

}

// Placeholder function to be called when the tab becomes active
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

let sse_loaded = false;

async function startEventSSE() {
    const url = `/outputs/stream/${fid}`;
    const response = await fetch(url);
    
    if (!response.ok) {
        throw new Error(`Failed to fetch stream: ${response.statusText}`);
    }

    if (sse_loaded) {
        return;
    }
    
    sse_loaded = true;

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        elmEvent.innerHTML += chunk; 
    }
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
