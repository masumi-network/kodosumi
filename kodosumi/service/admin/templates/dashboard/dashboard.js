// Dashboard JavaScript
const PAGE_SIZE = 100;
let refreshInterval = null;
let currentTimeRange = 24;
let currentOffset = 0;
let hasMore = false;
let isLoadingMore = false;
let currentFilters = {
    search: '',
    agent_name: '',
    user: '',
    status: ''
};
let globalStats = {
    total: 0,
    running: 0,
    error_rate: 0,
    avg_runtime: 0
};

// Utility functions
function formatTimestamp(timestamp) {
    if (!timestamp) return 'N/A';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString();
}

function formatDuration(seconds) {
    if (!seconds) return 'N/A';
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

function getStatusBadge(status) {
    const statusLower = status.toLowerCase();
    const badges = {
        'running': '<span class="badge primary-container">Running</span>',
        'awaiting': '<span class="badge secondary-container">Awaiting</span>',
        'finished': '<span class="badge tertiary-container">Finished</span>',
        'error': '<span class="badge error-container">Error</span>',
        'starting': '<span class="badge surface-variant">Starting</span>'
    };
    return badges[statusLower] || `<span class="badge surface-variant">${status}</span>`;
}

// API calls
async function fetchRunningAgents(limit = PAGE_SIZE, offset = 0) {
    const params = new URLSearchParams({
        hours: currentTimeRange,
        limit: limit,
        offset: offset,
        ...Object.fromEntries(Object.entries(currentFilters).filter(([_, v]) => v !== ''))
    });
    const response = await fetch(`/api/dashboard/running-agents?${params}`);
    return await response.json();
}

async function fetchErrors() {
    const response = await fetch(`/api/dashboard/errors?hours=${currentTimeRange}`);
    return await response.json();
}

async function fetchTimeline() {
    const response = await fetch(`/api/dashboard/timeline?hours=${currentTimeRange}`);
    return await response.json();
}

async function fetchAgentStats() {
    const response = await fetch('/api/dashboard/agent-stats');
    return await response.json();
}

async function fetchExecutionDetails(fid) {
    const response = await fetch(`/api/dashboard/execution/${fid}/details`);
    return await response.json();
}

async function fetchExecutionFiles(fid) {
    const response = await fetch(`/api/dashboard/execution/${fid}/files`);
    return await response.json();
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Render functions
function renderAgentRows(agents) {
    return agents.map(agent => {
        const hasFiles = agent.upload_count > 0 || agent.download_count > 0;
        const fileDisplay = hasFiles
            ? `<a href="#" onclick="showFiles('${agent.fid}'); return false;" style="color: var(--primary); text-decoration: underline;">${agent.upload_count}/${agent.download_count}</a>`
            : `<span style="color: var(--on-surface-variant);">${agent.upload_count}/${agent.download_count}</span>`;

        return `
        <tr>
            <td><strong>${agent.agent_name}</strong></td>
            <td><code>${agent.fid}</code></td>
            <td>${agent.user_name || agent.user_id}</td>
            <td class="center-align">${getStatusBadge(agent.status)}</td>
            <td>${formatTimestamp(agent.start_time)}</td>
            <td>${formatDuration(agent.runtime)}</td>
            <td class="center-align">${fileDisplay}</td>
            <td>
                <a href="/admin/status/view/${agent.fid}" class="button small">
                    <i class="tiny">visibility</i>
                </a>
            </td>
        </tr>
        `;
    }).join('');
}

function renderRunningAgents(data, append = false) {
    const tbody = document.querySelector('#running-agents-table tbody');
    const resultsCount = document.getElementById('results-count');

    hasMore = data.has_more || false;

    if (!append && data.agents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="center-align">No agents found</td></tr>';
        resultsCount.textContent = 'No results';
        return;
    }

    if (!append) {
        populateFilters(data.agents);
        tbody.innerHTML = renderAgentRows(data.agents);
    } else {
        tbody.insertAdjacentHTML('beforeend', renderAgentRows(data.agents));
    }

    const showing = (data.offset || 0) + data.agents.length;
    resultsCount.textContent = `Showing ${showing} of ${data.total} executions`;
}

async function loadMoreAgents() {
    if (isLoadingMore || !hasMore) return;
    isLoadingMore = true;
    try {
        currentOffset += PAGE_SIZE;
        const data = await fetchRunningAgents(PAGE_SIZE, currentOffset);
        renderRunningAgents(data, true);
    } catch (err) {
        console.error('Failed to load more agents:', err);
    } finally {
        isLoadingMore = false;
    }
}

async function showFiles(fid) {
    const modal = document.getElementById('files-modal');
    const modalFid = document.getElementById('files-modal-fid');
    const uploadList = document.getElementById('upload-files-list');
    const downloadList = document.getElementById('download-files-list');
    const uploadCount = document.getElementById('upload-count');
    const downloadCount = document.getElementById('download-count');

    modalFid.textContent = fid;
    uploadList.innerHTML = '<p>Loading...</p>';
    downloadList.innerHTML = '<p>Loading...</p>';
    modal.showModal();

    try {
        const data = await fetchExecutionFiles(fid);

        uploadCount.textContent = data.upload_count;
        downloadCount.textContent = data.download_count;

        // Render uploads
        if (data.uploads.length === 0) {
            uploadList.innerHTML = '<p class="small" style="text-align: center; color: var(--on-surface-variant); padding: 20px;">No uploaded files</p>';
        } else {
            uploadList.innerHTML = data.uploads.map(file => `
                <div class="file-card">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-size">${formatFileSize(file.size)}</div>
                    <a href="${file.url}" download class="download-btn">
                        <i>download</i>
                    </a>
                </div>
            `).join('');
        }

        // Render downloads
        if (data.downloads.length === 0) {
            downloadList.innerHTML = '<p class="small" style="text-align: center; color: var(--on-surface-variant); padding: 20px;">No output files</p>';
        } else {
            downloadList.innerHTML = data.downloads.map(file => `
                <div class="file-card">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-size">${formatFileSize(file.size)}</div>
                    <a href="${file.url}" download class="download-btn">
                        <i>download</i>
                    </a>
                </div>
            `).join('');
        }
    } catch (err) {
        uploadList.innerHTML = `<p class="error-text">Failed to load files: ${err.message}</p>`;
        downloadList.innerHTML = '';
    }
}

function populateFilters(agents) {
    // Get unique values
    const agentNames = [...new Set(agents.map(a => a.agent_name))].sort();
    const users = [...new Set(agents.map(a => a.user_name || a.user_id))].sort();
    const statuses = [...new Set(agents.map(a => a.status))].sort();

    // Populate agent filter
    const agentSelect = document.getElementById('filter-agent');
    const currentAgent = agentSelect.value;
    agentSelect.innerHTML = '<option value="">All Agents</option>' +
        agentNames.map(name => `<option value="${name}" ${name === currentAgent ? 'selected' : ''}>${name}</option>`).join('');

    // User filter is populated from agent-stats (sees all users, not just current page)

    // Populate status filter - only show statuses that exist in the data
    const statusSelect = document.getElementById('filter-status');
    const currentStatus = statusSelect.value;
    statusSelect.innerHTML = '<option value="">All Status</option>' +
        statuses.map(status => `<option value="${status}" ${status === currentStatus ? 'selected' : ''}>${status.charAt(0).toUpperCase() + status.slice(1)}</option>`).join('');
}

function renderErrors(data) {
    const container = document.getElementById('errors-container');

    if (data.errors.length === 0) {
        container.innerHTML = '<p class="center-align">No errors in selected time range</p>';
        return;
    }

    container.innerHTML = data.errors.map(error => {
        const errorMsg = error.error.error || error.error.message || JSON.stringify(error.error);
        const errorPreview = errorMsg.substring(0, 200) + (errorMsg.length > 200 ? '...' : '');

        return `
            <article class="border small-padding small-margin" style="cursor: pointer;"
                     onclick="showErrorDetail('${error.fid}', ${error.timestamp})">
                <nav class="no-space">
                    <div class="max">
                        <h6 class="small error-text">${error.fid}</h6>
                        <p class="small">${formatTimestamp(error.timestamp)} - User: ${error.user_id}</p>
                    </div>
                    <i>chevron_right</i>
                </nav>
                <p class="small"><code>${errorPreview}</code></p>
            </article>
        `;
    }).join('');
}

async function showErrorDetail(fid, timestamp) {
    const modal = document.getElementById('error-detail-modal');
    const content = document.getElementById('error-detail-content');

    content.innerHTML = '<p>Loading...</p>';
    modal.showModal();

    try {
        const data = await fetchExecutionDetails(fid);
        const errorEvent = data.events.find(e => e.kind === 'error' && e.timestamp === timestamp);

        if (errorEvent) {
            // Try to parse as JSON, fall back to plain text
            let errorDisplay;
            try {
                const errorData = JSON.parse(errorEvent.message);
                errorDisplay = JSON.stringify(errorData, null, 2);
            } catch {
                // Plain text error (e.g., traceback)
                errorDisplay = errorEvent.message;
            }

            content.innerHTML = `
                <p><strong>Execution ID:</strong> <code>${fid}</code></p>
                <p><strong>User:</strong> ${data.metadata.user_id}</p>
                <p><strong>Agent:</strong> ${data.metadata.agent_name || 'Unknown'}</p>
                <p><strong>Time:</strong> ${formatTimestamp(timestamp)}</p>
                <div class="small-divider"></div>
                <h6>Error Details:</h6>
                <pre style="background: var(--surface-container); padding: 16px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word;">${errorDisplay}</pre>
            `;
        } else {
            content.innerHTML = `<p class="error-text">Error event not found</p>`;
        }
    } catch (err) {
        content.innerHTML = `<p class="error-text">Failed to load error details: ${err.message}</p>`;
    }
}

function renderTimeline(data) {
    const container = document.getElementById('timeline-chart');

    if (data.executions.length === 0) {
        container.innerHTML = '<p class="center-align">No executions in selected time range</p>';
        return;
    }

    // Group executions by hour and status
    const hourlyData = {};
    data.executions.forEach(exec => {
        const hour = new Date(exec.start_time * 1000);
        hour.setMinutes(0, 0, 0);
        const hourKey = hour.toISOString();

        if (!hourlyData[hourKey]) {
            hourlyData[hourKey] = {
                finished: 0,
                running: 0,
                error: 0,
                awaiting: 0,
                starting: 0,
                other: 0
            };
        }

        const status = exec.status.toLowerCase();
        if (hourlyData[hourKey][status] !== undefined) {
            hourlyData[hourKey][status]++;
        } else {
            hourlyData[hourKey].other++;
        }
    });

    // Create D3 stacked bar chart
    const margin = { top: 20, right: 120, bottom: 50, left: 50 };
    const width = container.clientWidth - margin.left - margin.right;
    const height = 400 - margin.top - margin.bottom;

    container.innerHTML = '';
    const svg = d3.select('#timeline-chart')
        .append('svg')
        .attr('width', width + margin.left + margin.right)
        .attr('height', height + margin.top + margin.bottom)
        .append('g')
        .attr('transform', `translate(${margin.left},${margin.top})`);

    // Prepare data array
    const dataArray = Object.entries(hourlyData)
        .map(([hour, counts]) => ({
            hour: new Date(hour),
            ...counts,
            total: counts.finished + counts.running + counts.error + counts.awaiting + counts.starting + counts.other
        }))
        .sort((a, b) => a.hour - b.hour);

    // Stack keys and colors
    const keys = ['finished', 'running', 'awaiting', 'starting', 'error', 'other'];
    const colors = {
        'finished': '#4caf50',
        'running': '#2196f3',
        'awaiting': '#ff9800',
        'starting': '#9e9e9e',
        'error': '#f44336',
        'other': '#757575'
    };

    // X scale (band scale for bars)
    const x = d3.scaleBand()
        .domain(dataArray.map(d => d.hour))
        .range([0, width])
        .padding(0.2);

    // Y scale
    const y = d3.scaleLinear()
        .domain([0, d3.max(dataArray, d => d.total)])
        .nice()
        .range([height, 0]);

    // Stack generator
    const stack = d3.stack()
        .keys(keys);

    const stackedData = stack(dataArray);

    // Add axes
    const xAxis = svg.append('g')
        .attr('transform', `translate(0,${height})`)
        .call(d3.axisBottom(x).tickFormat(d3.timeFormat('%b %d %H:%M')).ticks(8));

    xAxis.selectAll('text')
        .style('fill', 'var(--on-surface)')
        .attr('transform', 'rotate(-45)')
        .style('text-anchor', 'end');

    svg.append('g')
        .call(d3.axisLeft(y).ticks(5))
        .selectAll('text')
        .style('fill', 'var(--on-surface)');

    // Create tooltip
    const tooltip = d3.select('body').append('div')
        .attr('class', 'timeline-tooltip')
        .style('position', 'absolute')
        .style('visibility', 'hidden')
        .style('background', 'var(--surface-container)')
        .style('border', '1px solid var(--outline)')
        .style('border-radius', '4px')
        .style('padding', '8px')
        .style('font-size', '12px')
        .style('pointer-events', 'none')
        .style('z-index', '1000');

    // Draw stacked bars
    svg.selectAll('g.layer')
        .data(stackedData)
        .enter()
        .append('g')
        .attr('class', 'layer')
        .attr('fill', d => colors[d.key])
        .selectAll('rect')
        .data(d => d)
        .enter()
        .append('rect')
        .attr('x', d => x(d.data.hour))
        .attr('y', d => y(d[1]))
        .attr('height', d => y(d[0]) - y(d[1]))
        .attr('width', x.bandwidth())
        .on('mouseover', function(event, d) {
            const status = d3.select(this.parentNode).datum().key;
            const count = d.data[status];
            const time = d3.timeFormat('%b %d, %H:%M')(d.data.hour);

            tooltip.html(`
                <strong>${time}</strong><br/>
                <span style="color: ${colors[status]}">●</span> ${status}: ${count}<br/>
                Total: ${d.data.total}
            `)
            .style('visibility', 'visible');
        })
        .on('mousemove', function(event) {
            tooltip
                .style('top', (event.pageY - 10) + 'px')
                .style('left', (event.pageX + 10) + 'px');
        })
        .on('mouseout', function() {
            tooltip.style('visibility', 'hidden');
        });

    // Add legend
    const legend = svg.append('g')
        .attr('transform', `translate(${width + 10}, 0)`);

    keys.forEach((key, i) => {
        const legendRow = legend.append('g')
            .attr('transform', `translate(0, ${i * 20})`);

        legendRow.append('rect')
            .attr('width', 12)
            .attr('height', 12)
            .attr('fill', colors[key]);

        legendRow.append('text')
            .attr('x', 18)
            .attr('y', 10)
            .style('fill', 'var(--on-surface)')
            .style('font-size', '12px')
            .text(key);
    });
}

function renderStats(data) {
    // Store global stats and user_map for reuse
    globalStats = {
        total: data.total_executions,
        running: data.by_status.running || 0,
        error_rate: data.error_rate,
        avg_runtime: data.avg_runtime
    };
    const userMap = data.user_map || {};

    // Update summary stats (will be updated again with filtered counts)
    updateStatsDisplay();

    // Render status table
    renderStatsTable('status-table-body', data.by_status);

    // Render user table — resolve UUIDs to names
    const userByName = {};
    for (const [uid, count] of Object.entries(data.by_user)) {
        userByName[userMap[uid] || uid] = count;
    }
    renderStatsTable('user-table-body', userByName);

    // Populate user filter from ALL users (not just current page)
    const userSelect = document.getElementById('filter-user');
    const currentUser = userSelect.value;
    const allUsers = Object.entries(userMap)
        .map(([id, name]) => ({ id, name }))
        .sort((a, b) => a.name.localeCompare(b.name));
    userSelect.innerHTML = '<option value="">All Users</option>' +
        allUsers.map(u => `<option value="${u.id}" ${u.id === currentUser ? 'selected' : ''}>${u.name}</option>`).join('');
}

function updateStatsDisplay(filteredData) {
    if (filteredData) {
        // Use server-side total (all matching, not just current page)
        const filteredTotal = filteredData.total;
        const filteredRunning = filteredData.agents.filter(a => a.status === 'running').length;
        const filteredErrors = filteredData.agents.filter(a => a.error).length;
        const filteredErrorRate = filteredTotal > 0 ? (filteredErrors / filteredTotal) * 100 : 0;
        const filteredAvgRuntime = filteredData.agents.reduce((sum, a) => sum + (a.runtime || 0), 0) / (filteredData.agents.length || 1);

        document.getElementById('stat-total').innerHTML = `${filteredTotal}<span class="small" style="color: var(--on-surface-variant);"> / ${globalStats.total}</span>`;
        document.getElementById('stat-running').innerHTML = `${filteredRunning}<span class="small" style="color: var(--on-surface-variant);"> / ${globalStats.running}</span>`;
        document.getElementById('stat-errors').innerHTML = `${filteredErrorRate.toFixed(1)}%<span class="small" style="color: var(--on-surface-variant);"> / ${(globalStats.error_rate * 100).toFixed(1)}%</span>`;
        document.getElementById('stat-runtime').innerHTML = `${formatDuration(filteredAvgRuntime)}<span class="small" style="color: var(--on-surface-variant);"> / ${formatDuration(globalStats.avg_runtime)}</span>`;
    } else {
        document.getElementById('stat-total').textContent = globalStats.total;
        document.getElementById('stat-running').textContent = globalStats.running;
        document.getElementById('stat-errors').textContent = `${(globalStats.error_rate * 100).toFixed(1)}%`;
        document.getElementById('stat-runtime').textContent = formatDuration(globalStats.avg_runtime);
    }
}

function renderStatsTable(tableBodyId, data) {
    const tbody = document.getElementById(tableBodyId);
    if (!data || Object.keys(data).length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="center-align">No data</td></tr>';
        return;
    }

    // Calculate total
    const total = Object.values(data).reduce((sum, count) => sum + count, 0);

    // Sort by count descending
    const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);

    tbody.innerHTML = entries.map(([key, count]) => {
        const percentage = total > 0 ? ((count / total) * 100).toFixed(1) : 0;
        return `
            <tr>
                <td><strong>${key}</strong></td>
                <td class="right-align">${count}</td>
                <td class="right-align">${percentage}%</td>
            </tr>
        `;
    }).join('');
}

// Load all dashboard data
async function loadDashboard() {
    try {
        currentOffset = 0;

        const [runningData, errorData, timelineData, statsData] = await Promise.all([
            fetchRunningAgents(PAGE_SIZE, 0),
            fetchErrors(),
            fetchTimeline(),
            fetchAgentStats()
        ]);

        // Handle "index building" state
        if (runningData.status === 'index_building') {
            const tbody = document.querySelector('#running-agents-table tbody');
            tbody.innerHTML = `<tr><td colspan="7" class="center-align"><progress></progress> ${runningData.message}</td></tr>`;
            return;
        }

        renderRunningAgents(runningData);
        renderErrors(errorData);
        renderTimeline(timelineData);
        renderStats(statsData);
        updateStatsDisplay(runningData);
    } catch (err) {
        console.error('Failed to load dashboard:', err);
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();

    // Refresh button
    document.getElementById('refresh-dashboard').addEventListener('click', (e) => {
        e.preventDefault();
        loadDashboard();
    });

    // Time range selector
    document.getElementById('time-range').addEventListener('change', (e) => {
        currentTimeRange = parseInt(e.target.value);
        loadDashboard();
    });

    // Filter event listeners
    document.getElementById('filter-search').addEventListener('input', (e) => {
        currentFilters.search = e.target.value;
        loadDashboard();
    });

    document.getElementById('filter-agent').addEventListener('change', (e) => {
        currentFilters.agent_name = e.target.value;
        loadDashboard();
    });

    document.getElementById('filter-user').addEventListener('change', (e) => {
        currentFilters.user = e.target.value;
        loadDashboard();
    });

    document.getElementById('filter-status').addEventListener('change', (e) => {
        currentFilters.status = e.target.value;
        loadDashboard();
    });

    // Auto-refresh every 30 seconds
    refreshInterval = setInterval(loadDashboard, 30000);

    // Infinite scroll for agents table — the scrollable div wraps the table
    const tableContainer = document.querySelector('#running-agents-table')?.parentElement;
    if (tableContainer) {
        tableContainer.addEventListener('scroll', () => {
            if (tableContainer.scrollTop + tableContainer.clientHeight >= tableContainer.scrollHeight - 100) {
                loadMoreAgents();
            }
        });
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
});

// Make functions available globally
window.showErrorDetail = showErrorDetail;
window.showFiles = showFiles;
