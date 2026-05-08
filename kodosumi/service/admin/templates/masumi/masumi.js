// ─────────────────────────────────────────────────────────────────────────────
// Masumi Payment Dashboard — JavaScript
// ─────────────────────────────────────────────────────────────────────────────

// ── State ─────────────────────────────────────────────────────────────────────
let currentNetwork = 'Mainnet';
let currentTab = 'overview';
let summaryData = null;
let agentData = null;
let trendData = null;
let walletData = null;

// Agent table sort state
let agentSortCol = 'lost_revenue';
let agentSortAsc = false;
let currentPeriod = '7d';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Format a USDM amount (already human-readable float from API) as dollar string.
 * e.g. 3.61 → "3.61$"
 */
function fmtUSDM(amount) {
  return (amount || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }) + '$';
}

/** Alias for backwards compatibility. */
function fmtUSDMFloat(amount) {
  return (amount || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }) + '$';
}

function fmtADA(ada) {
  return (ada || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }) + ' ADA';
}

function fmtCount(n) {
  return (n || 0).toLocaleString('en-US');
}

/**
 * Return an inline HTML badge with background colour depending on rate.
 * rate is 0–100 (percentage).
 */
function rateBadge(rate) {
  const r = rate || 0;
  let bg;
  if (r >= 95) {
    bg = '#4caf50';
  } else if (r >= 80) {
    bg = '#ff9800';
  } else {
    bg = '#f44336';
  }
  return `<span class="rate-badge" style="background:${bg};">${r.toFixed(0)}%</span>`;
}

/** Compute delivery rate (0-100) from a period dict. */
function deliveryRate(period) {
  const total = (period.delivered || 0)
    + (period.utxo_fail || 0)
    + (period.timeout || 0)
    + (period.buyer_invalid || 0)
    + (period.disputed || 0);
  if (total === 0) return null;
  return (period.delivered / total) * 100;
}

/** Truncate a string to at most `max` chars, appending … */
function truncate(str, max) {
  if (!str) return '—';
  if (str.length <= max) return str;
  return str.slice(0, 8) + '…' + str.slice(-8);
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {});
}

// ── Network switching ─────────────────────────────────────────────────────────

function switchNetwork(network) {
  currentNetwork = network;
  document.querySelectorAll('#network-tabs [data-network]').forEach(el => {
    el.classList.toggle('active', el.dataset.network === network);
  });
  // Clear cached data so everything re-fetches for the new network
  summaryData = agentData = trendData = walletData = null;
  refreshData();
}

// ── Content tab switching ─────────────────────────────────────────────────────

function showTab(tab) {
  currentTab = tab;

  // Update tab styling
  document.querySelectorAll('#content-tabs [data-tab]').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });

  // Show / hide content panels
  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.toggle('active', el.id === `tab-${tab}`);
  });

  // Lazy-load tab data if not already loaded
  if (tab === 'agents' && !agentData) loadAgents();
  if (tab === 'trend' && !trendData) loadTrend();
  if (tab === 'wallets' && !walletData) loadWallets();
}

// ── API loaders ───────────────────────────────────────────────────────────────

async function loadSummary() {
  try {
    summaryData = await fetchJSON(`/api/masumi/summary?network=${encodeURIComponent(currentNetwork)}`);
    renderCards();
    renderPeriodTable();
    renderFailureBreakdown();
  } catch (err) {
    console.error('Masumi summary fetch failed:', err);
    renderSummaryError(err.message);
  }
}

async function loadAgents() {
  setLoading('agents-loading', true);
  setVisible('agents-table', false);
  try {
    agentData = await fetchJSON(`/api/masumi/agents?network=${encodeURIComponent(currentNetwork)}&period=${currentPeriod}`);
    renderAgentTable();
  } catch (err) {
    console.error('Masumi agents fetch failed:', err);
    document.getElementById('agents-loading').innerHTML =
      `<i class="error-text">error</i><span class="error-text">Failed to load: ${err.message}</span>`;
  }
}

function switchPeriod(period) {
  currentPeriod = period;
  document.querySelectorAll('[data-period]').forEach(el => {
    el.classList.toggle('active', el.dataset.period === period);
  });
  agentData = null;
  loadAgents();
}

async function loadTrend() {
  setLoading('trend-loading', true);
  setVisible('trend-chart-container', false);
  setVisible('trend-legend', false);
  try {
    trendData = await fetchJSON(`/api/masumi/trend?network=${encodeURIComponent(currentNetwork)}`);
    renderTrendChart();
  } catch (err) {
    console.error('Masumi trend fetch failed:', err);
    document.getElementById('trend-loading').innerHTML =
      `<i class="error-text">error</i><span class="error-text">Failed to load: ${err.message}</span>`;
  }
}

async function loadWallets() {
  setLoading('wallets-loading', true);
  setVisible('wallets-content', false);
  try {
    walletData = await fetchJSON(`/api/masumi/wallets?network=${encodeURIComponent(currentNetwork)}`);
    renderWalletTable();
  } catch (err) {
    console.error('Masumi wallets fetch failed:', err);
    document.getElementById('wallets-loading').innerHTML =
      `<i class="error-text">error</i><span class="error-text">Failed to load: ${err.message}</span>`;
  }
}

async function refreshData() {
  summaryData = agentData = trendData = walletData = null;
  await loadSummary();
  if (currentTab === 'agents') await loadAgents();
  if (currentTab === 'trend') await loadTrend();
  if (currentTab === 'wallets') await loadWallets();
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function setLoading(id, show) {
  const el = document.getElementById(id);
  if (el) el.style.display = show ? 'flex' : 'none';
}

function setVisible(id, show) {
  const el = document.getElementById(id);
  if (el) el.style.display = show ? '' : 'none';
}

function renderSummaryError(msg) {
  ['card-revenue', 'card-rate', 'card-lost', 'card-pending'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = 'Error';
  });
  ['period-loading', 'failure-loading'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<i class="error-text">error</i><span class="error-text">${msg}</span>`;
  });
}

// ── Cards ─────────────────────────────────────────────────────────────────────

function renderCards() {
  if (!summaryData) return;
  const cards = summaryData.cards || {};

  // Revenue
  document.getElementById('card-revenue').textContent = fmtUSDM(cards.revenue);
  document.getElementById('card-revenue-sub').textContent =
    `${fmtCount(cards.delivered)} delivered`;

  // Delivery rate
  const rate = cards.delivery_rate;
  if (rate !== null && rate !== undefined) {
    const rateEl = document.getElementById('card-rate');
    const rateColor = rate >= 95
      ? '#4caf50'
      : rate >= 80 ? '#ff9800' : '#f44336';
    rateEl.textContent = rate.toFixed(1) + '%';
    rateEl.style.color = rateColor;
  } else {
    document.getElementById('card-rate').textContent = '—';
  }
  document.getElementById('card-rate-sub').textContent = 'All time';

  // Lost revenue
  document.getElementById('card-lost').textContent = fmtUSDM(cards.lost_revenue);
  const lostCount = (cards.total_paid || 0) - (cards.delivered || 0);
  document.getElementById('card-lost-sub').textContent = `${fmtCount(lostCount)} failures`;

  // Pending
  document.getElementById('card-pending').textContent = fmtCount(cards.pending);
  document.getElementById('card-pending-sub').textContent = 'Awaiting result';
}

// ── Period table ──────────────────────────────────────────────────────────────

function renderPeriodTable() {
  setLoading('period-loading', false);
  if (!summaryData) return;

  const periods = [
    { key: 'today',     label: 'Today' },
    { key: 'yesterday', label: 'Yesterday' },
    { key: '7d',        label: '7 Days' },
    { key: '30d',       label: '30 Days' },
    { key: 'total',     label: 'All Time' },
  ];

  const periodMap = {};
  (summaryData.periods || []).forEach(p => { periodMap[p.period] = p; });

  const tbody = document.getElementById('period-tbody');
  tbody.innerHTML = periods.map(({ key, label }) => {
    const p = periodMap[key] || {};
    const rate = p.rate;
    const rateCellHTML = (rate !== null && rate !== undefined && (p.delivered || p.utxo_fail || p.timeout))
      ? rateBadge(rate)
      : '<span style="opacity:.5">—</span>';
    return `
      <tr>
        <td><strong>${label}</strong></td>
        <td class="right-align">${fmtUSDM(p.revenue)}</td>
        <td class="right-align">${fmtCount(p.delivered)}</td>
        <td class="right-align">${fmtCount(p.utxo_fail)}</td>
        <td class="right-align">${fmtCount(p.timeout)}</td>
        <td class="right-align">${fmtCount(p.buyer_invalid)}</td>
        <td class="center-align">${rateCellHTML}</td>
      </tr>
    `;
  }).join('');

  setVisible('period-table', true);
}

// ── Failure breakdown ─────────────────────────────────────────────────────────

function renderFailureBreakdown() {
  setLoading('failure-loading', false);
  if (!summaryData) return;

  const fb = summaryData.failure_breakdown || {};

  const categories = [
    {
      key: 'masumi_utxo',
      label: fb.masumi_utxo?.label || 'Masumi UTxO',
      color: 'var(--error)',
      count: fb.masumi_utxo?.count || 0,
      amount: fb.masumi_utxo?.amount || 0,
      responsibility: 'masumi',
    },
    {
      key: 'masumi_state',
      label: fb.masumi_state_error?.label || 'Masumi State Error',
      color: 'var(--error-container)',
      count: fb.masumi_state_error?.count || 0,
      amount: fb.masumi_state_error?.amount || 0,
      responsibility: 'masumi',
    },
    {
      key: 'timeout',
      label: fb.unresolved_timeout?.label || 'Timeout / Agent Error',
      color: 'var(--tertiary)',
      count: fb.unresolved_timeout?.count || 0,
      amount: fb.unresolved_timeout?.amount || 0,
      responsibility: 'agent',
    },
    {
      key: 'buyer_invalid',
      label: fb.buyer_invalid?.label || 'Buyer Invalid',
      color: 'var(--outline)',
      count: fb.buyer_invalid?.count || 0,
      amount: 0,
      responsibility: 'buyer',
    },
    {
      key: 'disputed',
      label: fb.disputed?.label || 'Disputed',
      color: 'var(--outline-variant)',
      count: fb.disputed?.count || 0,
      amount: fb.disputed?.amount || 0,
      responsibility: 'buyer',
    },
  ];

  const totalFailures = categories.reduce((s, c) => s + c.count, 0);

  // Proportional bar
  const barEl = document.getElementById('failure-bar');
  if (totalFailures > 0) {
    barEl.innerHTML = categories
      .filter(c => c.count > 0)
      .map((c, i, arr) => {
        let radius = '';
        if (arr.length === 1) radius = 'border-radius: 4px;';
        else if (i === 0) radius = 'border-radius: 4px 0 0 4px;';
        else if (i === arr.length - 1) radius = 'border-radius: 0 4px 4px 0;';
        return `<div style="flex:${c.count}; background:${c.color}; ${radius}"
                     title="${c.label}: ${fmtCount(c.count)}"></div>`;
      }).join('');
  } else {
    barEl.innerHTML = '<div style="flex:1; background:var(--surface-container); border-radius:4px; height:24px;"></div>';
  }

  // Legend
  const legendEl = document.getElementById('failure-legend');
  legendEl.innerHTML = categories.map(c => `
    <div class="s12 m6 l4" style="padding: 4px 0;">
      <div class="row" style="align-items: center; gap: 6px; flex-wrap: nowrap;">
        <span class="legend-swatch" style="background:${c.color};"></span>
        <span class="small">${c.label} — <strong>${fmtCount(c.count)}</strong>
          ${c.amount > 0 ? `<span style="opacity:.7">(${fmtUSDM(c.amount)})</span>` : ''}
        </span>
      </div>
    </div>
  `).join('');

  // Responsibility summary
  const masumiCount = categories.filter(c => c.responsibility === 'masumi')
    .reduce((s, c) => s + c.count, 0);
  const masumiAmount = categories.filter(c => c.responsibility === 'masumi')
    .reduce((s, c) => s + c.amount, 0);
  const buyerCount = categories.filter(c => c.responsibility === 'buyer')
    .reduce((s, c) => s + c.count, 0);
  const agentCount = categories.filter(c => c.responsibility === 'agent')
    .reduce((s, c) => s + c.count, 0);
  const agentAmount = categories.filter(c => c.responsibility === 'agent')
    .reduce((s, c) => s + c.amount, 0);

  function pct(n) {
    if (totalFailures === 0) return '0.0%';
    return ((n / totalFailures) * 100).toFixed(1) + '%';
  }

  const respEl = document.getElementById('failure-resp');
  respEl.innerHTML = `
    <div class="small-divider"></div>
    <div class="resp-row">
      <i style="color:var(--error);">build</i>
      <span>Masumi Infrastructure: <strong>${fmtCount(masumiCount)}</strong> (${pct(masumiCount)})
        ${masumiAmount > 0 ? `— Lost: <strong>${fmtUSDM(masumiAmount)}</strong>` : ''}
      </span>
    </div>
    <div class="resp-row">
      <i style="color:var(--tertiary);">warning</i>
      <span>Buyer / External: <strong>${fmtCount(buyerCount)}</strong> (${pct(buyerCount)})</span>
    </div>
    <div class="resp-row">
      <i style="color:var(--outline);">bug_report</i>
      <span>Agent Errors / Timeout: <strong>${fmtCount(agentCount)}</strong> (${pct(agentCount)})
        ${agentAmount > 0 ? `— Lost: <strong>${fmtUSDM(agentAmount)}</strong>` : ''}
      </span>
    </div>
  `;

  setVisible('failure-content', true);
}

// ── Agent table ───────────────────────────────────────────────────────────────

function sortAgents(col) {
  if (agentSortCol === col) {
    agentSortAsc = !agentSortAsc;
  } else {
    agentSortCol = col;
    agentSortAsc = col === 'name'; // strings default ascending, numbers descending
  }
  // Update header styling
  document.querySelectorAll('#agents-table th[data-col]').forEach(th => {
    const active = th.dataset.col === col;
    th.classList.toggle('sort-active', active);
    const arrow = th.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = active ? (agentSortAsc ? '↑' : '↓') : '↕';
  });
  renderAgentTable();
}

function renderAgentTable() {
  setLoading('agents-loading', false);
  if (!agentData) return;

  const agents = [...(agentData.agents || [])];

  // Sort
  agents.sort((a, b) => {
    let va = a[agentSortCol];
    let vb = b[agentSortCol];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return agentSortAsc ? -1 : 1;
    if (va > vb) return agentSortAsc ? 1 : -1;
    return 0;
  });

  const tbody = document.getElementById('agents-tbody');
  if (agents.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="center-align" style="opacity:.6;">No agent data</td></tr>';
  } else {
    tbody.innerHTML = agents.map(ag => {
      const rateCellHTML = (ag.rate !== null && ag.rate !== undefined)
        ? rateBadge(ag.rate)
        : '<span style="opacity:.5">—</span>';
      const exposeName = ag.expose_name || ag.name || '';
      const timelineUrl = `/admin/timeline/view?agent_name=${encodeURIComponent(ag.name || '')}`;
      return `
        <tr>
          <td><a href="${timelineUrl}" style="color:var(--primary); text-decoration:none;"><strong>${ag.name || '—'}</strong></a></td>
          <td class="right-align">${fmtUSDM(ag.revenue)}</td>
          <td class="right-align">${fmtCount(ag.delivered)}</td>
          <td class="right-align">${fmtCount(ag.utxo_fail)}</td>
          <td class="right-align">${fmtCount(ag.timeout)}</td>
          <td class="right-align">${fmtCount(ag.buyer_invalid)}</td>
          <td class="center-align">${rateCellHTML}</td>
          <td class="right-align">${fmtUSDM(ag.lost_revenue)}</td>
        </tr>
      `;
    }).join('');
  }

  setVisible('agents-table', true);
}

// ── Trend chart (D3 v7) ───────────────────────────────────────────────────────

function renderTrendChart() {
  setLoading('trend-loading', false);
  if (!trendData || !trendData.weeks || trendData.weeks.length === 0) {
    document.getElementById('trend-loading').innerHTML =
      '<span style="opacity:.6;">No trend data available</span>';
    setLoading('trend-loading', true);
    return;
  }

  const container = document.getElementById('trend-chart-container');
  const legendEl = document.getElementById('trend-legend');
  container.style.display = '';
  legendEl.style.display = 'flex';

  const weeks = trendData.weeks;
  const svgEl = document.getElementById('trend-chart-svg');

  // Dimensions — responsive to container width
  const totalWidth = container.clientWidth || 800;
  const margin = { top: 20, right: 60, bottom: 56, left: 52 };
  const width = totalWidth - margin.left - margin.right;
  const height = 300 - margin.top - margin.bottom;

  // Clear previous render
  svgEl.innerHTML = '';
  d3.select(svgEl)
    .attr('viewBox', `0 0 ${totalWidth} ${300}`)
    .attr('preserveAspectRatio', 'xMidYMid meet');

  const svg = d3.select(svgEl)
    .append('g')
    .attr('transform', `translate(${margin.left},${margin.top})`);

  // Data
  const keys = ['delivered', 'utxo_fail', 'timeout'];
  const colors = {
    delivered: 'var(--primary)',
    utxo_fail: 'var(--error)',
    timeout:   'var(--tertiary)',
  };

  // Scales
  const xScale = d3.scaleBand()
    .domain(weeks.map(d => d.week))
    .range([0, width])
    .padding(0.25);

  const maxTotal = d3.max(weeks, d =>
    (d.delivered || 0) + (d.utxo_fail || 0) + (d.timeout || 0)
  ) || 1;

  const yLeft = d3.scaleLinear()
    .domain([0, maxTotal * 1.1])
    .nice()
    .range([height, 0]);

  const yRight = d3.scaleLinear()
    .domain([0, 100])
    .range([height, 0]);

  // Stack
  const stack = d3.stack().keys(keys);
  const stacked = stack(weeks.map(d => ({
    week:      d.week,
    delivered: d.delivered || 0,
    utxo_fail: d.utxo_fail || 0,
    timeout:   d.timeout || 0,
    rate:      d.rate || 0,
  })));

  // Grid lines
  svg.append('g')
    .attr('class', 'trend-grid')
    .call(d3.axisLeft(yLeft).ticks(5).tickSize(-width).tickFormat(''));

  // Bars
  stacked.forEach((layer) => {
    svg.selectAll(null)
      .data(layer)
      .join('rect')
      .attr('x', d => xScale(d.data.week))
      .attr('y', d => yLeft(d[1]))
      .attr('height', d => yLeft(d[0]) - yLeft(d[1]))
      .attr('width', xScale.bandwidth())
      .attr('fill', colors[layer.key])
      .attr('opacity', 0.88);
  });

  // Rate line overlay
  const lineGen = d3.line()
    .x(d => (xScale(d.week) || 0) + xScale.bandwidth() / 2)
    .y(d => yRight(d.rate || 0))
    .curve(d3.curveMonotoneX);

  const lineData = weeks.map(d => ({
    week: d.week,
    rate: d.rate || 0,
  }));

  svg.append('path')
    .datum(lineData)
    .attr('fill', 'none')
    .attr('stroke', 'var(--secondary)')
    .attr('stroke-width', 2)
    .attr('stroke-opacity', 0.8)
    .attr('stroke-dasharray', '4 2')
    .attr('d', lineGen);

  // Dots on the line
  svg.selectAll('.rate-dot')
    .data(lineData)
    .join('circle')
    .attr('class', 'rate-dot')
    .attr('cx', d => (xScale(d.week) || 0) + xScale.bandwidth() / 2)
    .attr('cy', d => yRight(d.rate))
    .attr('r', 3)
    .attr('fill', 'var(--secondary)')
    .attr('opacity', 0.9);

  // Left Y axis
  svg.append('g')
    .attr('class', 'trend-axis')
    .call(d3.axisLeft(yLeft).ticks(5));

  // Right Y axis (rate %)
  svg.append('g')
    .attr('class', 'trend-axis')
    .attr('transform', `translate(${width}, 0)`)
    .call(d3.axisRight(yRight).ticks(5).tickFormat(d => d + '%'));

  // X axis
  const xTick = weeks.length > 12
    ? d => {
        const idx = weeks.findIndex(w => w.week === d);
        return idx % 2 === 0 ? d : '';
      }
    : d => d;

  svg.append('g')
    .attr('class', 'trend-axis')
    .attr('transform', `translate(0,${height})`)
    .call(d3.axisBottom(xScale).tickFormat(xTick))
    .selectAll('text')
    .attr('transform', 'rotate(-40)')
    .style('text-anchor', 'end')
    .attr('dx', '-4px')
    .attr('dy', '8px');

  // Tooltip
  const tooltipDiv = d3.select('body').selectAll('.masumi-trend-tooltip').data([null]);
  const tooltip = tooltipDiv.join('div')
    .attr('class', 'masumi-trend-tooltip')
    .style('position', 'absolute')
    .style('visibility', 'hidden')
    .style('background', 'var(--surface-container)')
    .style('border', '1px solid var(--outline)')
    .style('border-radius', '6px')
    .style('padding', '8px 12px')
    .style('font-size', '12px')
    .style('pointer-events', 'none')
    .style('z-index', '1000')
    .style('min-width', '130px');

  // Hover zones per bar
  svg.selectAll('.hover-zone')
    .data(lineData)
    .join('rect')
    .attr('class', 'hover-zone')
    .attr('x', d => xScale(d.week) || 0)
    .attr('y', 0)
    .attr('width', xScale.bandwidth())
    .attr('height', height)
    .attr('fill', 'transparent')
    .on('mouseover', function(event, d) {
      const wd = weeks.find(w => w.week === d.week) || {};
      tooltip.html(`
        <strong>${d.week}</strong><br/>
        <span style="color:var(--primary)">&#9632;</span> Delivered: ${fmtCount(wd.delivered)}<br/>
        <span style="color:var(--error)">&#9632;</span> UTxO Fail: ${fmtCount(wd.utxo_fail)}<br/>
        <span style="color:var(--tertiary)">&#9632;</span> Timeout: ${fmtCount(wd.timeout)}<br/>
        <hr style="margin:4px 0; border-color:var(--outline-variant)"/>
        Rate: <strong>${d.rate.toFixed(1)}%</strong>
      `).style('visibility', 'visible');
    })
    .on('mousemove', function(event) {
      tooltip
        .style('top', (event.pageY - 12) + 'px')
        .style('left', (event.pageX + 12) + 'px');
    })
    .on('mouseout', function() {
      tooltip.style('visibility', 'hidden');
    });
}

// ── Wallet table ──────────────────────────────────────────────────────────────

function renderWalletTable() {
  setLoading('wallets-loading', false);
  if (!walletData) return;

  const content = document.getElementById('wallets-content');
  let html = '';

  function walletSection(title, icon, wallets) {
    if (!wallets || wallets.length === 0) return '';
    let totalAda = 0;
    let totalUsdm = 0;

    const rows = wallets.map(w => {
      const adaAmt = w.ada_lovelace || 0;
      const usdmAmt = w.usdm_base || 0;
      totalAda += adaAmt;
      totalUsdm += usdmAmt;
      const adaDisplay = adaAmt / 1_000_000;
      const lowBal = adaDisplay < 20;
      return `
        <tr>
          <td>${w.note || w.name || '—'}</td>
          <td>
            <div class="addr-cell">
              <span class="addr-text" title="${w.address || ''}">${truncate(w.address, 18)}</span>
              ${w.address ? `
                <button class="copy-btn" onclick="copyToClipboard('${w.address}')" title="Copy address">
                  <i style="font-size:16px;">content_copy</i>
                </button>
              ` : ''}
            </div>
          </td>
          <td class="right-align" style="${lowBal ? 'color:var(--tertiary);' : ''}">
            ${lowBal ? '<i style="font-size:14px; vertical-align:middle; color:#f59e0b;">warning</i> ' : ''}
            ${fmtADA(adaAmt)}
          </td>
          <td class="right-align">${fmtUSDM(usdmAmt)}</td>
        </tr>
      `;
    }).join('');

    return `
      <div class="wallet-section-head">
        <i>${icon}</i>
        <span>${title}</span>
      </div>
      <div style="overflow-x: auto; margin-bottom: 12px;">
        <table class="border masumi-table" style="width: 100%;">
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th class="right-align">ADA</th>
              <th class="right-align">USDM</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
          <tfoot>
            <tr style="font-weight: 600; border-top: 2px solid var(--outline-variant);">
              <td colspan="2">Total</td>
              <td class="right-align">${fmtADA(totalAda)}</td>
              <td class="right-align">${fmtUSDM(totalUsdm)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    `;
  }

  html += walletSection('Selling Wallets', 'sell', walletData.selling_wallets);
  html += walletSection('Buying Wallets', 'shopping_cart', walletData.buying_wallets);

  if (!html) {
    html = '<p class="center-align" style="opacity:.6; padding: 24px 0;">No wallet data available</p>';
  }

  content.innerHTML = html;
  setVisible('wallets-content', true);
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function loadNetworks() {
  try {
    const data = await fetchJSON('/api/masumi/networks');
    const nav = document.getElementById('network-tabs');
    const nets = data.networks || [];
    if (nets.length === 0) {
      nav.innerHTML = '<span style="opacity:.5; padding: 8px;">No Masumi networks configured</span>';
      return;
    }
    const icons = { 'Mainnet': 'public', 'Preprod': 'science' };
    nav.innerHTML = nets.map((n, i) => {
      const icon = icons[n.registry_network] || 'lan';
      const active = i === 0 ? ' class="active"' : '';
      return `<a${active} data-network="${n.registry_network}" onclick="switchNetwork('${n.registry_network}')">` +
        `<i>${icon}</i><span>${n.name}</span></a>`;
    }).join('');
    currentNetwork = nets[0].registry_network;
  } catch (e) {
    console.error('Failed to load networks:', e);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  await loadNetworks();
  loadSummary();
});

// Expose globals for inline onclick handlers
window.switchNetwork = switchNetwork;
window.showTab = showTab;
window.sortAgents = sortAgents;
window.switchPeriod = switchPeriod;
window.refreshData = refreshData;
window.copyToClipboard = copyToClipboard;
