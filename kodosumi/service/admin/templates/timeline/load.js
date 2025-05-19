const MIN_NEXT_INTERVAL = 200;
const MIN_UPDATE_INTERVAL = 1200;
const PER_PAGE = 25;
const LoadingState = {
    IDLE: 'idle',
    LOADING: 'loading',
    ERROR: 'error',
    COMPLETE: 'complete'
};
let isLoading = false;
let currentPage = 1;
let origin = null;
let offset = null;
let timestamp = null;
let lastLoadTime = 0;
let currentQuery = '';
let observer = null;
let endOfFile = null;
let endOfList = null;
let container = null;
let updateTimer = null;
let hasReachedEnd = false;
let loadingTimeout = null;
let isSearching = false;
let isLoadAll = false;
let currentLoadingState = LoadingState.IDLE;
let isInitialized = false;
let closeIcon = null;
let searchInput = null;
let selectAll = null;
let debounceTimeout = null;
let selectAllLabel = null;
let deleteSelect = null;

function startUpdateTimer() {
    if (updateTimer) return;
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

function updateLoadingState(newState, mode) {
    if (currentLoadingState === newState) return;
    
    currentLoadingState = newState;
    const progressBar = document.getElementById('load-progress');
    
    if (!progressBar) {
        console.warn('Progress bar element not found');
        return;
    }
    if (mode === "update") {
        return;
    }
    if (isLoadAll) {
        return;
    }
    switch (newState) {
        case LoadingState.LOADING:
            progressBar.style.display = 'block';
            progressBar.removeAttribute('value'); 
            break;
        case LoadingState.IDLE:
        case LoadingState.COMPLETE:
        case LoadingState.ERROR:
            if (loadingTimeout) clearTimeout(loadingTimeout);
            loadingTimeout = setTimeout(() => {
                progressBar.style.display = 'none';
            }, 300);
            break;
    } 
    if (!isLoadAll) {   
        startUpdateTimer();
    }
}

function handleSearch() {
    if (observer) {
        observer.disconnect();
    }
    stopUpdateTimer();    
    if (isLoading) {
        setTimeout(handleSearch, 100);
        return;
    }
    offset = null;
    origin = null;
    timestamp = null;
    hasReachedEnd = false;
    isSearching = true;
    currentQuery = searchInput ? searchInput.value : '';
    endOfFile.style.display = 'none';
    container.innerHTML = '';
    updateLoadingState(LoadingState.LOADING, "next");
    loadMoreTimelineItems("next");
    // Observer wieder verbinden
    if (observer) {
        observer.observe(endOfList);
    }
}

function search() {
    clearTimeout(debounceTimeout);
    debounceTimeout = setTimeout(handleSearch, 300);
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
    const isVisible = (
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );
    
    if (isVisible) {
        if (!hasReachedEnd && !isLoading) {
            debouncedLoadMore();
            return;
        }
    } 
    if (!isLoadAll) {   
        startUpdateTimer();
    }
}

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
            const existingItem = container.querySelector(`[name="${item.fid}"]`);
            console.log("insert", item.fid, existingItem == null);
            if (!existingItem) {
                const li = createTimelineItem(item);
                container.insertBefore(li, container.firstChild);
            }
        },
        append: (item) => {
            const existingItem = container.querySelector(`[name="${item.fid}"]`);
            if (mode === "next") {
                const li = createTimelineItem(item);
                container.appendChild(li);
                // console.log("append", item.fid, existingItem == null, container, li);
            }
        },
        delete: (fid) => {
            const existingItem = container.querySelector(`[name="${fid}"]`);
            console.log("delete", fid, existingItem == null);
            if (existingItem) {
                existingItem.remove();
            }
        }
    };
    if (items.update && items.update.length > 0) {
        items.update.forEach(item => processMap.update(item));
    }
    if (items.insert && items.insert.length > 0) {
        items.insert.forEach(item => processMap.insert(item));
    }
    if (items.delete && items.delete.length > 0) {
        items.delete.forEach(fid => processMap.delete(fid));
    }
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
    <h5 class="summary small bold">${item.summary || '...'}</h5>
    <!-- <span class="small">${item.fid}</span> -->
    <div style="text-wrap: balance; word-break: break-word; overflow-wrap: break-word; max-width: 100%;" class="inputs italic">${inputs}</div>
    </div>
    <div class="max"></div>
    <div class="follow">
    <label class="runtime">${formatRuntime(item.runtime)}</label>
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
    const statusChip = element.querySelector('.chip');
    const svg = element.querySelector('svg');
    statusChip.className = `left-align chip ${format}`;
    statusChip.innerHTML = `<i>${statusIcon}</i>${item.status}`;
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
    console.log("update", item);
    // update: inputs and summary
    const runtimeLabel = element.querySelector('.runtime');
    runtimeLabel.textContent = formatRuntime(item.runtime);
    // update: inputs and summary
    const summaryLabel = element.querySelector('.summary');
    summaryLabel.textContent = item.summary;
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
                    window.location.href = `/outputs/status/view/${fid}`;
                }, 300);
            } else {
                clearTimeout(clickTimer);
                clickTimer = null;
                window.open(`/outputs/status/view/${fid}`, '_blank');
            }
        };
    });
}

async function loadAll() {
    if (isLoading) return;
    
    isLoadAll = true;
    const progressBar = document.getElementById('load-progress');
    if (progressBar) {
        progressBar.style.display = 'block';
        progressBar.removeAttribute('value');
    }
    try {
        while (!hasReachedEnd) {
            await loadMoreTimelineItems("next");
        }
    } finally {
        isLoadAll = false;
        checkAll();
        if (progressBar) {
            progressBar.style.display = 'none';
        }
    }
}

async function loadMoreTimelineItems(mode, pp = PER_PAGE) {
    if (isLoading) return;
    
    try {
        isLoading = true;
        if (!isLoadAll) {
            updateLoadingState(LoadingState.LOADING, mode);
        }
        lastLoadTime = Date.now();
        const params = new URLSearchParams();
        params.append('pp', pp);
        params.append('mode', mode);
        if (currentQuery) params.append('q', currentQuery);
        if (origin) {
            params.append('origin', origin);
        }
        if (offset) {
            params.append('offset', offset);
        }
        if (timestamp) params.append('timestamp', timestamp);
        const url = `/timeline?${params.toString()}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('/timeline error');
        data = await response.json();
        
        // console.log('Server response:', {
        //     mode,
        //     end: hasReachedEnd,
        //     timestamp: data.timestamp,
        //     updates: data.items?.update?.length || 0,
        //     inserts: data.items?.insert?.length || 0,
        //     deletes: data.items?.delete?.length || 0,
        //     appends: data.items?.append?.length || 0
        // });
        
        if (data.origin) {
            if (mode === "update" || !origin) {
                origin = data.origin;
            }
        }
        processTimelineItems(data.items, mode);
        
        if (!data.offset) {
            hasReachedEnd = true;
            const count = container.querySelectorAll('li').length;
            endOfFile.textContent = `(${count} item${count > 1 ? 's' : ''})`;
            endOfFile.style.display = 'block';
            if (observer) {
                observer.unobserve(endOfList);
            }
        } else {
            hasReachedEnd = false;
        }
        offset = data.offset;
        timestamp = data.timestamp;

        if (!isLoadAll) {
            updateLoadingState(LoadingState.COMPLETE, mode);
        }
    } catch (error) {
        console.error('Error loading timeline items:', data, error);
        if (!isLoadAll) {
            updateLoadingState(LoadingState.ERROR, mode);
        }
    } finally {
        isLoading = false;
        if (isSearching && hasReachedEnd) {
            isSearching = false;
        }
        if (mode === "next" && !hasReachedEnd) {
            checkVisibility();
        } else {
            startUpdateTimer();
        }
        updateAction();
    }
}

document.addEventListener('DOMContentLoaded', (event) => {
    container = document.getElementById('timeline');
    endOfFile = document.getElementById('end-of-file');
    endOfList = document.getElementById('end-of-list');
    closeIcon = document.getElementById('close-icon');
    searchInput = document.getElementById('search-input');
    selectAll = document.getElementById('select-all');
    observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !hasReachedEnd) {
                debouncedLoadMore();
            } 
        });
    }, {
        root: null,
        rootMargin: '100px',
        threshold: 0.1
    });
    const searchForm = document.querySelector('form[role="search"]');
    if (searchForm) {
        searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            search();
        });
    }
    window.addEventListener('pageshow', function(event) {
        if (isInitialized) return;
        isInitialized = true;
        stopUpdateTimer();
        container.innerHTML = '';
        endOfFile.style.display = 'none';
        currentQuery = searchInput.value;
        observer.observe(endOfList);
        loadMoreTimelineItems("next");
    });
});
