function startUpdateTimer() {
    if (updateTimer) return;
    // console.log("startUpdateTimer");
    updateTimer = setInterval(() => {
        loadMoreTimelineItems("update");
    }, MIN_UPDATE_INTERVAL);
}

function stopUpdateTimer() {
    if (updateTimer) {
        clearInterval(updateTimer);
        updateTimer = null;
    }
}

function search() {
    stopUpdateTimer();    
    if (isLoading) {
        setTimeout(search, 100);
        return;
    }
    offset = null;
    origin = null;
    timestamp = null;
    hasReachedEnd = false;
    const searchInput = document.getElementById('search-input');
    currentQuery = searchInput ? searchInput.value : '';
    endOfFile.style.display = 'none';
    container.innerHTML = '';
    if (observer) {
        observer.disconnect();
        observer.observe(endOfList);
    }
    loadMoreTimelineItems("next");
}

function debouncedLoadMore() {
    const now = Date.now();
    const timeSinceLastLoad = now - lastLoadTime;
    if (!hasReachedEnd) {
        if (isLoading) {
            setTimeout(() => {
                loadMoreTimelineItems("next");
            }, MIN_NEXT_INTERVAL);
        }
        else {
            if (timeSinceLastLoad >= MIN_NEXT_INTERVAL) {
                loadMoreTimelineItems("next");
            } else {
                setTimeout(() => {
                    loadMoreTimelineItems("next");
                }, MIN_NEXT_INTERVAL - timeSinceLastLoad);
            }
        }
    }
}

function checkVisibility() {
    const rect = endOfList.getBoundingClientRect();
    // console.log(rect);
    const isVisible = (
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );
    if (isVisible) {
        // console.log("visible");
        if (!hasReachedEnd) {
            if (!isLoading) {
                debouncedLoadMore();
                return;
            }
        }
    } 
    startUpdateTimer();
}

// Konstanten für Status und Formatierung
const STATUS = {
    RUNNING: {
        icon: 'play_circle',
        format: 'tertiary fill',
        progressClass: (progress) => progress != null ? 'progress-circle' : 'spinner-circle'
    },
    FINISHED: {
        icon: 'check_circle',
        format: 'secondary',
        progressClass: () => 'empty-circle'
    },
    ERROR: {
        icon: 'error',
        format: 'error',
        progressClass: () => 'empty-circle'
    }
};

function getStatusConfig(item) {
    const status = `${item.status}`.toLowerCase();
    return STATUS[status.toUpperCase()] || STATUS.ERROR;
}

function createProgressElement(item) {
    const config = getStatusConfig(item);
    const progressValue = item.progress != null ? `value="${item.progress}"` : '';
    const progressClass = config.progressClass(item.progress);
    
    return {
        statusIcon: config.icon,
        format: config.format,
        progressClass,
        progressValue
    };
}

function processTimelineItems(items, mode) {
    if (!items) return;
    
    const processMap = {
        update: (item) => {
            const existingItem = container.querySelector(`[name="${item.fid}"]`);
            console.log("update", item.fid, existingItem == null);
            if (existingItem) {
                updateTimelineItem(existingItem, item);
            }
        },
        insert: (item) => {
            // Prüfen, ob das Item bereits existiert
            const existingItem = container.querySelector(`[name="${item.fid}"]`);
            console.log("insert", item.fid, existingItem == null);
            if (!existingItem) {
                const li = createTimelineItem(item);
                container.insertBefore(li, container.firstChild);
            }
        },
        append: (item) => {
            const existingItem = container.querySelector(`[name="${item.fid}"]`);
            console.log("append", item.fid, existingItem == null);
            if (mode === "next") {
                const li = createTimelineItem(item);
                container.appendChild(li);
            }
        }
    };
    
    // Zuerst Updates verarbeiten
    if (items.update && items.update.length > 0) {
        items.update.forEach(item => processMap.update(item));
    }
    
    // Dann Inserts verarbeiten
    if (items.insert && items.insert.length > 0) {
        items.insert.forEach(item => processMap.insert(item));
    }
    
    // Zuletzt Appends verarbeiten
    if (items.append && items.append.length > 0) {
        items.append.forEach(item => processMap.append(item));
    }
}

function createTimelineItem(item) {
    const li = document.createElement('li');
    li.classList.add('top-align');
    li.setAttribute('name', item.fid);
    
    const { statusIcon, format, progressClass, progressValue } = createProgressElement(item);

    if (item.inputs) {
        let validInputs = Object.fromEntries(
            Object.entries(Object.values(item.inputs)[0])
                .filter(([_, value]) => value !== null)
        );
        inputs = formatInputs(validInputs);
    }
    else {
        inputs = "";
    }
    
    li.innerHTML = `
        <label class="checkbox large">
            <input type="checkbox"/>
            <span></span>
        </label>
        <div class="follow small-round"> 
            <p class="left-align chip ${format}" style="width: 110px;">
                <i>${statusIcon}</i>${item.status}
            </p>
        </div>
        <div class="follow">
            <h5 class="small bold">${item.summary}</h5>
            <!--<span class="small">${item.fid}</span>-->
            <div style="text-wrap: balance; word-break: break-word; overflow-wrap: break-word; max-width: 100%;" class="italic">${inputs}</div>
        </div>
        <div class="max"></div>
        <div class="follow">
            <label>${formatRuntime(item.runtime)}</label>
            <svg class="${progressClass}" ${progressValue}></svg>
            <label>${formatDateTime(item.startup)}</label>
            <span class="max">&nbsp;</span>
        </div>
    `;
    
    const svg = li.querySelector('svg');
    if (progressClass === "progress-circle") {
        create_progress_circle(svg);
    } else if (progressClass === "spinner-circle") {
        create_spinner_circle(svg);
    } else if (progressClass === "empty-circle") {
        create_empty_circle(svg);
    }
    
    addClickHandlers(li, item.fid);
    return li;
}

function updateTimelineItem(element, item) {
    const { statusIcon, format, progressClass, progressValue } = createProgressElement(item);
    
    // Update status and progress
    const statusChip = element.querySelector('.chip');
    const svg = element.querySelector('svg');
    
    statusChip.className = `left-align chip ${format}`;
    statusChip.innerHTML = `<i>${statusIcon}</i>${item.status}`;
    
    // Update progress circle
    svg.className = progressClass;
    if (progressValue) {
        svg.setAttribute('value', item.progress);
    }
    
    if (progressClass === "progress-circle") {
        create_progress_circle(svg);
    } else if (progressClass === "spinner-circle") {
        create_spinner_circle(svg);
    } else if (progressClass === "empty-circle") {
        create_empty_circle(svg);
    }
    
    // Update runtime
    const runtimeLabel = element.querySelector('.follow:last-child label:first-child');
    runtimeLabel.textContent = formatRuntime(item.runtime);
}

function addClickHandlers(element, fid) {
    const followElements = element.querySelectorAll('.follow');
    let clickTimer = null;
    
    followElements.forEach(element => {
        element.style.cursor = 'pointer';
        element.onclick = (e) => {
            e.preventDefault();
            if (clickTimer === null) {
                clickTimer = setTimeout(() => {
                    clickTimer = null;
                    window.location.href = `/outputs/status/${fid}`;
                }, 300);
            } else {
                clearTimeout(clickTimer);
                clickTimer = null;
                window.open(`/outputs/status/${fid}`, '_blank');
            }
        };
    });
}

async function loadMoreTimelineItems(mode) {
    if (isLoading) {
        showPulse();
        if (mode === "next") {
            debouncedLoadMore();
        }
        return;
    }
    hidePulse();
    let data = null;
    try {
        isLoading = true;
        lastLoadTime = Date.now();
        const params = new URLSearchParams();
        params.append('pp', PER_PAGE);
        params.append('mode', mode);
        if (currentQuery) params.append('q', currentQuery);
        if (mode === "update") {
            params.append('origin', origin);
        }
        if (offset) {
            params.append('offset', offset);
        }
        if (timestamp) params.append('timestamp', timestamp);
        const url = `/timeline?${params.toString()}`;
        // showPulse();
        const response = await fetch(url);
        if (!response.ok) throw new Error('/timeline error');
        data = await response.json();
        // console.log("Received data", mode, data);
        
        // Update pagination parameters from response
        if (data.origin) {
            // Nur origin aktualisieren, wenn wir im update Modus sind
            // oder wenn wir noch keine origin haben
            if (mode === "update" || !origin) {
                origin = data.origin;
            }
        }
        if (data.offset) offset = data.offset;
        if (data.timestamp) timestamp = data.timestamp;
        
        // Process all items
        processTimelineItems(data.items, mode);
        
        // Check if we've reached the end of the list
        if (!data.offset) {
            // console.log("Reached end of list");
            hasReachedEnd = true;
            const count = container.querySelectorAll('li').length;
            // console.log("reached end of list", count);
            endOfFile.textContent = `(${count} item${count > 1 ? 's' : ''})`;
            endOfFile.style.display = 'block';
            observer.unobserve(endOfList);
        }
        // hidePulse();
        
    } catch (error) {
        console.error('Error loading timeline:', error, data);
    } finally {
        isLoading = false;
        if (mode === "next" && !hasReachedEnd) {
            checkVisibility();
        }
        else {
            startUpdateTimer();
        }
    }
}

document.addEventListener('DOMContentLoaded', (event) => {
    endOfFile = document.getElementById('end-of-file');
    endOfList = document.getElementById('end-of-list');
    closeIcon = document.getElementById('close-icon');
    searchInput = document.getElementById('search-input');
    container = document.getElementById('timeline');
    observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !hasReachedEnd) {
                // console.log("Intersection detected", { hasReachedEnd });
                debouncedLoadMore();
            } 
        })
    }, {
        root: null,
        rootMargin: '100px',
        threshold: 0.1
    });
        
    observer.observe(endOfList);
    const searchForm = document.querySelector('form[role="search"]');
    if (searchForm) {
        searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            search();
        });
    }
    // Start initial load
    loadMoreTimelineItems("next");
});
