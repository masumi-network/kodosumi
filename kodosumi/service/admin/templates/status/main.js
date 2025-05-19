let ioSource = null;
let ioDone = false;
let sse_loaded = false;

let elmArticleHead = null;
let elmToggleIcon = null;
let elmDetailsElement = null;
let elmStartup = null;
let elmRuntime = null;
let elmFinish = null;
let elmSize = null;
let elmStatus = null;
let elmStatusValid = null;
let elmProgress = null;
let elmAbout = null;

let elmOutputPage = null;
let elmOutput = null;
let elmOutputEnd = null;
let elmFinal = null;
let elmFinalResult = null;

let elmStdioPage = null;
let elmStdio = null;
let elmStdioEnd = null;

let elmEventPage = null;
let elmEvent = null;

let elmOutputArticle = null;
let elmStdioArticle = null;
let elmEventArticle = null;
let tabModes = null;

let elmFID = null;
let elmEntryPoint = null;
let elmTags = null;
let elmSummary = null;
let elmDescription = null;
let elmAuthor = null;
let elmOrganization = null;
let elmVersion = null;
let elmKodosumiVersion = null;

let elmInputs = null;
let status = null;
let eventSource = null;
let startup = null;
let windowSize = 90;
let sparkInterval = 1000;
let autoSpark = null;
let eventActivity = new Map();
let total = 0;

let last_active = null;
let active = false;
let follow = {
    "page-stdio": true,
    "page-event": true,
    "page-output": true
};
let scrollDebounceTimer = null;
const scrollDebounceMs = 100;
let flow_active = true;

let trashButton = null;
let elmStatusIcon = null;

let mainStreamReady = false;

let stdioBuffer = [];
const STDIO_BUFFER_SIZE = 10;
let lastStdioFlush = Date.now();
const MIN_FLUSH_INTERVAL = 100; // Minimum Zeit zwischen Flushes in ms
let stdioFlushTimer = null;
const MAX_FLUSH_DELAY = 1000; // Maximale Verzögerung für Flush in ms

let outputBuffer = [];
const OUTPUT_BUFFER_SIZE = 10;
let lastOutputFlush = Date.now();
let outputFlushTimer = null;

function shouldFlushBuffer(lastFlush) {
    return Date.now() - lastFlush >= MIN_FLUSH_INTERVAL;
}

function scheduleGuaranteedFlush(bufferType) {
    if (bufferType === 'stdio') {
        if (stdioFlushTimer) {
            clearTimeout(stdioFlushTimer);
        }
        stdioFlushTimer = setTimeout(() => {
            if (stdioBuffer.length > 0) {
                flushStdioBuffer();
            }
        }, MAX_FLUSH_DELAY);
    } else if (bufferType === 'output') {
        if (outputFlushTimer) {
            clearTimeout(outputFlushTimer);
        }
        outputFlushTimer = setTimeout(() => {
            if (outputBuffer.length > 0) {
                flushOutputBuffer();
            }
        }, MAX_FLUSH_DELAY);
    }
}

function addToStdioBuffer(text, isError = false) {
    stdioBuffer.push({ text, isError });
    if (stdioBuffer.length >= STDIO_BUFFER_SIZE || shouldFlushBuffer(lastStdioFlush)) {
        flushStdioBuffer();
    } else {
        scheduleGuaranteedFlush('stdio');
    }
}

function flushStdioBuffer() {
    if (stdioBuffer.length === 0) return;
    
    const fragment = document.createDocumentFragment();
    stdioBuffer.forEach(({ text, isError }) => {
        const span = document.createElement('span');
        span.className = isError ? 'error-text' : 'primary-text';
        span.innerHTML = text;
        fragment.appendChild(span);
        fragment.appendChild(document.createElement('br'));
    });
    elmStdio.appendChild(fragment);
    stdioBuffer = [];
    lastStdioFlush = Date.now();
    scrollDown();
}

function addToOutputBuffer(text) {
    outputBuffer.push(text);
    if (outputBuffer.length >= OUTPUT_BUFFER_SIZE || shouldFlushBuffer(lastOutputFlush)) {
        flushOutputBuffer();
    } else {
        scheduleGuaranteedFlush('output');
    }
}

function flushOutputBuffer() {
    if (outputBuffer.length === 0) return;
    
    const fragment = document.createDocumentFragment();
    outputBuffer.forEach(text => {
        const div = document.createElement('div');
        div.innerHTML = text;
        fragment.appendChild(div);
    });
    elmOutput.appendChild(fragment);
    outputBuffer = [];
    lastOutputFlush = Date.now();
    scrollDown();
}

function parseData(event, updateTotal = true) {
    const [id, ts, js] = splitData(event);
    const currentSecond = Math.floor(ts);
    if (ts == null) {
        return [null, null];
    }
    if (startup == null) {
        startup = ts;
        applyToAll(elmStartup, (elm) => {elm.innerText = formatUnixTime(ts)});
        const startSecond = currentSecond - (windowSize - 1);
        for (let sec = startSecond; sec <= currentSecond; sec++) {
            if (!eventActivity.has(sec)) {
                eventActivity.set(sec, 0);
            }
        }
    }
    if (startup && flow_active && ts) {
        applyToAll(
            elmRuntime, 
            (elm) => {elm.innerText = secondsToHHMMSS(ts - startup)});
    }
    const oldestAllowedSecond = currentSecond - (windowSize - 1);
    for (const key of eventActivity.keys()) {
        if (key < oldestAllowedSecond) {
            eventActivity.delete(key);
        }
    }
    if (!eventActivity.has(currentSecond)) {
        eventActivity.set(currentSecond, 0);
    }
    total += event.data.length;
    eventActivity.set(currentSecond, (eventActivity.get(currentSecond) || 0) + event.data.length);
    applyToAll(elmSize, (elm) => {elm.innerText = (total / 1024).toFixed(1)}); 
    if (autoSpark == null) {
        redrawDynamicCharts();
    }
    return [ts, js];
}

function startAutoSpark() {
    if (autoSpark == null) {
        const tick = () => {
            const nowSeconds = Math.floor(Date.now() / 1000);
            if (!eventActivity.has(nowSeconds)) {
                eventActivity.set(nowSeconds, 0);
            }
            redrawDynamicCharts();
            if (flow_active) {
                applyToAll(
                    elmRuntime, 
                    (elm) => {elm.innerText = secondsToHHMMSS(nowSeconds - startup)});
            }
            autoSpark = setTimeout(tick, sparkInterval);
        };
        autoSpark = setTimeout(tick, sparkInterval);
    }
}

function stopAutoSpark() {
    if (autoSpark) {
        clearTimeout(autoSpark);
        autoSpark = null;
    }
}

function checkMainStreamReady() {
    return new Promise((resolve) => {
        if (mainStreamReady) {
            resolve(true);
            return;
        }
        
        const checkInterval = setInterval(() => {
            if (mainStreamReady) {
                clearInterval(checkInterval);
                resolve(true);
            }
        }, 100);
    });
}

function startSTDIO() {
    checkMainStreamReady().then(() => {
        if (ioSource != null || ioDone) {
            return;
        }
        ioSource = new EventSource(`/outputs/stdio/${fid}`);
        ioSource.onopen = function() {
            console.log("stdio SSE stream opened.");
        };
        ioSource.addEventListener('stdout', function(event) {
            const [id, ts, js] = splitData(event);
            if (js != null) {
                addToStdioBuffer(js);
            }
        });
        ioSource.addEventListener('debug', function(event) {
            const [id, ts, js] = splitData(event);
            if (js != null) {
                addToStdioBuffer(js);
            }
        });
        ioSource.addEventListener('stderr', function(event) {
            const [id, ts, js] = splitData(event);
            if (js != null) {
                addToStdioBuffer(js, true);
            }
        });
        ioSource.addEventListener('error', function(event) {
            const [id, ts, js] = splitData(event);
            if (js != null) {
                addToStdioBuffer(js, true);
            }
        });
        ioSource.addEventListener('eof', function(event) {
            flushStdioBuffer();
            ioSource.close();
            ioSource = null;
            ioDone = true;
            console.log("stdio SSE stream closed (eof).");
        });
        ioSource.addEventListener('alive', function(event) {
            flushStdioBuffer();
        });
    });
}

async function startEventSSE() {
    if (sse_loaded) {
        return;
    }
    
    await checkMainStreamReady();
    
    const url = `/outputs/stream/${fid}`;
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Failed to fetch stream: ${response.statusText}`);
    }
    
    console.log("Event SSE stream opened.");
    sse_loaded = true;
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        elmEvent.innerHTML += chunk; 
    }
    console.log("Event SSE stream closed.");
}

const stdio_observer = new MutationObserver((mutationsList, stdio_observer) => {
    for(const mutation of mutationsList) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
            const targetElement = mutation.target;
            if (targetElement.classList.contains('active') && ioSource == null) {
                startSTDIO();
            }
        }
    }
});

document.addEventListener('DOMContentLoaded', (event) => {

    elmArticleHead = document.getElementById('article-head');
    elmToggleIcon = document.getElementById('details-toggle');
    elmDetailsElement = document.getElementById('header-details');
    elmStartup = document.getElementsByClassName('startup');
    elmRuntime = document.getElementsByClassName('runtime');
    elmFinish = document.getElementsByClassName('finish');
    elmSize = document.getElementsByClassName('size');
    elmStatus = document.getElementsByClassName('status');
    elmStatusValid = document.getElementById('status-valid');
    elmStatusIcon = document.getElementById('status-icon');
    elmProgress = document.getElementById('progress');
    elmAbout = document.getElementById('about');
    
    elmOutputPage = document.querySelector('#page-output');
    elmOutput = document.getElementById('output');
    elmOutputEnd = document.getElementById('output-end');
    elmFinal = document.getElementById('final');
    elmFinalResult = document.getElementsByClassName('final-result');

    elmStdioPage = document.querySelector('#page-stdio');
    elmStdio = document.getElementById('stdio');
    elmStdioEnd = document.getElementById('stdio-end');

    elmEventPage = document.getElementById('page-event');
    elmEvent = document.getElementById('event');

    elmFID = document.getElementsByClassName('fid');
    elmEntryPoint = document.getElementsByClassName('entry_point');
    elmTags = document.getElementsByClassName('tags');
    elmSummary = document.getElementsByClassName('summary');
    elmDescription = document.getElementsByClassName('description');
    elmAuthor = document.getElementsByClassName('author');
    elmOrganization = document.getElementsByClassName('organization');
    elmVersion = document.getElementsByClassName('version');
    elmKodosumiVersion = document.getElementsByClassName('kodosumi');

    elmInputs = document.getElementsByClassName('inputs');
    eventSource = new EventSource(`/outputs/main/${fid}?extended=true`);

    elmToggleIcon.addEventListener('click', () => {
        elmDetailsElement.open = !elmDetailsElement.open;
        elmToggleIcon.textContent = elmDetailsElement.open ? 'arrow_drop_down' : 'arrow_right';
    });
    eventSource.onopen = function() {
        console.log("main SSE stream opened.");
        mainStreamReady = true;
    };
    eventSource.onerror = function() {
        console.log("main SSE stream error.");
    };
    eventSource.addEventListener('status', function(event) {
        const [ts, js] = parseData(event);
        applyToAll(elmStatus, (elm) => {elm.innerText = js});
        if (js === "finished" || js === "error") {
            flow_active = false;
            stopAutoSpark();
            elmProgress.value = 100;
            applyToAll(elmFinish, (elm) => {elm.innerText = formatUnixTime(ts)});
            applyToAll(
                elmRuntime, 
                (elm) => {elm.innerText = secondsToHHMMSS(ts - startup)});
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
            addToOutputBuffer(js);
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
        flushOutputBuffer();
        console.log('main SSE stream closed (eof).');
        eventSource.close();
        stopAutoSpark();
    });
    
    stdio_observer.observe(elmStdioPage, { attributes: true, attributeFilter: ['class'] });
    if (elmStdioPage.classList.contains('active')) {
        startSTDIO();
    }

    const event_observer = new MutationObserver((mutationsList, event_observer) => {
        // console.log("event_observer", mutationsList);
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
});
