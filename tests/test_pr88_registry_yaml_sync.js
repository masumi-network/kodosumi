const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scriptPath =
    'kodosumi/service/admin/templates/expose/_registry_script.html';
const source = fs.readFileSync(scriptPath, 'utf8');
const dialogSource = fs.readFileSync(
    'kodosumi/service/admin/templates/expose/_registry_dialog_script.html',
    'utf8',
);
const start = source.indexOf('function blockRegistrySync(');
const end = source.indexOf('\n}\n\n// Clear a stuck', start) + 2;
assert.notEqual(start, -1);
assert.ok(end > start);
const functionSource = source.slice(start, end);
const badgeStart = source.indexOf('function updateBadge(');
const badgeEnd = source.indexOf('\n}\n\n// The rail', badgeStart) + 2;
assert.notEqual(badgeStart, -1);
assert.ok(badgeEnd > badgeStart);
const badgeSource = source.slice(badgeStart, badgeEnd);
const generationStart = source.indexOf('const registryRequestGenerations');
const generationEnd = source.indexOf('\nfunction getNetworkEl', generationStart);
assert.notEqual(generationStart, -1);
assert.ok(generationEnd > generationStart);
const generationSource = source.slice(generationStart, generationEnd);
const statusStart = source.indexOf('async function checkRegistryStatus(');
const statusEnd = source.indexOf('\nfunction updateRegistryUI(', statusStart);
assert.notEqual(statusStart, -1);
assert.ok(statusEnd > statusStart);
const statusSource = source.slice(statusStart, statusEnd);
const registryUiStart = statusEnd + 1;
const registryUiEnd = source.indexOf('\nfunction updateBadge(', registryUiStart);
const registryUiSource = source.slice(registryUiStart, registryUiEnd);
const railStart = source.indexOf('function updateRailBadge(');
const railEnd = source.indexOf('\nfunction updateMigrationUI(', railStart);
const railSource = source.slice(railStart, railEnd);
const migrationUiStart = source.indexOf('function updateMigrationUI(');
const migrationUiEnd = source.indexOf('\nfunction copyAgentId(', migrationUiStart);
const migrationUiSource = source.slice(migrationUiStart, migrationUiEnd);
const deregisterStart = dialogSource.indexOf('async function deregisterAgent(');
assert.notEqual(deregisterStart, -1);
const deregisterSource = dialogSource.slice(deregisterStart);

function runSync(
    textarea,
    serverYaml,
    expectedYaml,
    updatedEtag,
    previousEtag,
    currentEtag = 'old-etag',
) {
    let error = null;
    const etag = {value: currentEtag};
    const context = {
        document: {
            querySelector: () => textarea,
            getElementById: (id) => id === 'etag' ? etag : null,
        },
        showRegError: (_idx, message) => { error = message; },
    };
    vm.runInNewContext(functionSource, context);
    const result = context.syncMetaYaml(
        0, serverYaml, expectedYaml, updatedEtag, previousEtag,
    );
    return {error, etag: etag.value, result};
}

{
    const textarea = {value: 'user edit', defaultValue: 'saved'};
    const outcome = runSync(
        textarea, 'server update', undefined, 'new-etag', 'old-etag');
    assert.equal(textarea.value, 'user edit');
    assert.equal(textarea.defaultValue, 'saved');
    assert.match(outcome.error, /copy.*reload/i);
    assert.equal(outcome.etag, 'old-etag');
    assert.equal(outcome.result, false);
}

{
    const textarea = {value: 'saved', defaultValue: 'saved'};
    const outcome = runSync(
        textarea, 'server update', undefined, 'new-etag', 'old-etag');
    assert.equal(textarea.value, 'server update');
    assert.equal(textarea.defaultValue, 'server update');
    assert.equal(outcome.error, null);
    assert.equal(outcome.etag, 'new-etag');
    assert.equal(outcome.result, true);
}

{
    const textarea = {value: 'newer yaml', defaultValue: 'newer yaml'};
    const outcome = runSync(
        textarea,
        'older yaml',
        undefined,
        'etag-1',
        'etag-0',
        'etag-2',
    );
    assert.equal(textarea.value, 'newer yaml');
    assert.equal(outcome.etag, 'etag-2');
    assert.match(outcome.error, /reload/i);
    assert.equal(outcome.result, false);
}

{
    const textarea = {value: 'submitted', defaultValue: 'older'};
    const outcome = runSync(textarea, 'registered', 'submitted');
    assert.equal(textarea.value, 'registered');
    assert.equal(textarea.defaultValue, 'registered');
    assert.equal(outcome.result, true);
}

{
    const textarea = {value: 'typed while waiting', defaultValue: 'older'};
    const outcome = runSync(textarea, 'registered', 'submitted');
    assert.equal(textarea.value, 'typed while waiting');
    assert.equal(outcome.result, false);
}

{
    const textarea = {value: 'edited', defaultValue: 'saved', dataset: {}};
    const etag = {value: 'old-etag'};
    const context = {
        document: {
            querySelector: () => textarea,
            getElementById: (id) => id === 'etag' ? etag : null,
        },
        showRegError: () => {},
    };
    vm.runInNewContext(functionSource, context);
    assert.equal(context.syncMetaYaml(
        0, 'server', undefined, 'new-etag', 'old-etag'), false);
    assert.equal(context.syncMetaYaml(0, null), false);
    textarea.value = 'saved';
    assert.equal(context.syncMetaYaml(
        0, 'server', undefined, 'new-etag', 'old-etag'), true);
    assert.equal(context.syncMetaYaml(0, null), true);
}

assert.equal(
    dialogSource.match(
        /syncMetaYaml\(\s*idx, data\.updatedYaml, metaYaml, data\.updatedEtag,\s*data\.previousEtag\)/g,
    )
        .length,
    2,
);
assert.doesNotMatch(dialogSource, /\.value\s*=\s*data\.updatedYaml/);
assert.doesNotMatch(dialogSource, /window\.location\.reload/);

const migrationStart = dialogSource.indexOf('async function submitMigration()');
const migrationEnd = dialogSource.indexOf(
    '\nasync function deregisterPrevious',
    migrationStart,
);
const migrationSource = dialogSource.slice(migrationStart, migrationEnd);
assert.match(migrationSource, /var metaYaml = textarea \? textarea\.value : '';/);

const pollingStart = source.indexOf('function startPolling(');
const pollingSource = source.slice(pollingStart);
assert.match(pollingSource, /pollInFlight\[idx\]/);
assert.doesNotMatch(pollingSource, /window\.location\.reload/);
assert.match(pollingSource, /Registry update is taking longer/);
assert.doesNotMatch(pollingSource, /Registration is taking longer/);
const cancelStart = source.indexOf('async function cancelMigration(');
const cancelEnd = source.indexOf('\n\n// ─── Polling', cancelStart);
const cancelSource = source.slice(cancelStart, cancelEnd);

for (const [state, expectedClass, expectedText] of [
    ['DeregistrationIntent', 'reg-pending', 'Deregistering'],
    ['DeregistrationRequested', 'reg-pending', 'Deregistering'],
    ['DeregistrationInitiated', 'reg-pending', 'Deregistering'],
    ['DeregistrationFailed', 'reg-failed', 'Deregistration Failed'],
    ['DeregistrationConfirmed', 'reg-not-registered', 'Deregistered'],
]) {
    const badge = {className: '', innerHTML: '', textContent: ''};
    const agentId = {style: {}, textContent: '', title: ''};
    const copyButton = {style: {}};
    const context = {
        document: {
            getElementById: (id) => ({
                reg_badge_0: badge,
                reg_agent_id_0: agentId,
                reg_copy_0: copyButton,
            })[id] || null,
        },
    };
    vm.runInNewContext(badgeSource, context);
    context.updateBadge(0, state, 'agent-1');
    assert.match(badge.className, new RegExp(expectedClass), state);
    assert.match(badge.innerHTML || badge.textContent, new RegExp(expectedText), state);
}

const syncCalls = [...source.matchAll(/syncMetaYaml\((?!\s*idx, yamlText)([\s\S]*?)\);/g)];
assert.ok(syncCalls.length > 0);
for (const call of syncCalls) {
    assert.match(call[1], /previousEtag/, call[0]);
}
const dialogSyncCalls = [
    ...dialogSource.matchAll(/syncMetaYaml\(([\s\S]*?)\)(?:;|\s*\{)/g),
];
assert.ok(dialogSyncCalls.length > 0);
for (const call of dialogSyncCalls) {
    assert.match(call[1], /previousEtag/, call[0]);
}

assert.equal(
    (dialogSource.match(/var errorUpdate = data\.extra \|\| \{\};/g) || []).length,
    2,
);
assert.match(
    dialogSource,
    /if \(errorUpdate\.updatedYaml\) \{\s*startPolling\(idx, flowUrl, true\);/,
);
assert.match(
    dialogSource,
    /if \(errorUpdate\.updatedYaml\) \{\s*startPolling\(idx, flowUrl, false\);/,
);
assert.doesNotMatch(dialogSource, /dataset\.agentId/);
assert.ok(
    (dialogSource.match(/activeDialogIdx !== idx/g) || []).length >= 4,
);
assert.match(dialogSource, /activeDialogIdx = null;[\s\S]*register-dialog/);
assert.match(dialogSource, /activeDialogIdx = null;[\s\S]*migrate-dialog/);
assert.ok((dialogSource.match(/blockRegistrySync\(/g) || []).length >= 4);
assert.ok((dialogSource.match(/meta_etag:/g) || []).length >= 4);

{
    const rail = {style: {}, className: '', textContent: '', title: ''};
    const context = {
        document: {getElementById: () => rail},
    };
    vm.runInNewContext(railSource, context);
    context.updateRailBadge(0, {
        state: 'DeregistrationConfirmed',
        paymentSourceType: 'Web3CardanoV1',
    });
    assert.equal(rail.style.display, 'none');
}

{
    const deregisterButton = {style: {}, disabled: true, innerHTML: ''};
    const registerButton = {style: {}};
    const actions = {style: {}};
    const info = {style: {}, textContent: 'Waiting for on-chain deregistration...'};
    const context = {
        document: {
            getElementById: (id) => ({
                dereg_btn_0: deregisterButton,
                reg_btn_0: registerButton,
                reg_actions_0: actions,
                reg_info_0: info,
            })[id] || null,
        },
        updateBadge: () => {},
        updateRailBadge: () => {},
        updateMigrationUI: () => {},
        checkNetworkLock: () => {},
    };
    vm.runInNewContext(registryUiSource, context);
    context.updateRegistryUI(0, {state: 'DeregistrationFailed'});
    assert.equal(deregisterButton.style.display, '');
    assert.equal(deregisterButton.disabled, false);
    assert.match(deregisterButton.innerHTML, /Deregister/);
    context.updateRegistryUI(0, {
        state: 'DeregistrationFailed',
        errorMessage: 'insufficient collateral',
        transaction: {txHash: 'abc', status: 'failed'},
    });
    assert.match(info.textContent, /insufficient collateral/);
    context.updateRegistryUI(0, {state: 'DeregistrationConfirmed'});
    assert.equal(registerButton.style.display, '');
    assert.equal(deregisterButton.style.display, 'none');
    assert.equal(actions.style.display, '');
    assert.equal(info.style.display, 'none');
    assert.equal(info.textContent, '');
    info.style.display = 'block';
    info.textContent = 'Waiting for on-chain deregistration...';
    context.updateRegistryUI(0, {state: 'NotRegistered'});
    assert.equal(info.style.display, 'none');
    info.style.display = 'block';
    info.textContent = 'No selling wallets found for this network.';
    context.updateRegistryUI(0, {state: 'NotRegistered'});
    assert.equal(info.style.display, 'block');
    assert.match(info.textContent, /No selling wallets/);
}

{
    const previousButton = {style: {}, disabled: true, innerHTML: ''};
    const activeButton = {style: {display: ''}};
    const previousRow = {style: {}};
    const previousId = {};
    const context = {
        document: {
            getElementById: (id) => ({
                prev_reg_row_0: previousRow,
                prev_reg_id_0: previousId,
                prev_dereg_btn_0: previousButton,
                dereg_btn_0: activeButton,
            })[id] || null,
        },
        showRegError: () => {},
        hideRegError: () => {},
    };
    vm.runInNewContext(migrationUiSource, context);
    context.updateMigrationUI(0, {
        state: 'RegistrationConfirmed',
        previousRegistration: {
            agentIdentifier: 'old-agent',
            deregisterRequested: false,
            deregistrationState: 'DeregistrationFailed',
        },
    });
    assert.equal(previousButton.disabled, false);
    assert.match(previousButton.innerHTML, /Deregister V1/);
    assert.equal(activeButton.style.display, 'none');
    context.updateMigrationUI(0, {
        state: 'RegistrationConfirmed',
        previousRegistration: {
            agentIdentifier: 'old-agent',
            deregisterRequested: true,
            deregistrationState: 'DeregistrationRequested',
        },
    });
    assert.equal(previousButton.disabled, true);
    assert.match(previousButton.innerHTML, /Deregistering/);
    let migrationError = '';
    context.showRegError = (_idx, message) => { migrationError = message; };
    context.updateMigrationUI(0, {
        state: 'RegistrationConfirmed',
        migration: {migrationError: 'Registry row mismatch'},
    });
    assert.match(migrationError, /Registry row mismatch/);
}

function deferred() {
    var resolve;
    var promise = new Promise((done) => { resolve = done; });
    return {promise, resolve};
}

function registryResponse(data, ok = true) {
    return {ok, json: async () => data};
}

async function testRegistryResponseOrdering() {
    const first = deferred();
    const second = deferred();
    const responses = [first.promise, second.promise];
    const events = [];
    const context = {
        exposeName: 'expose',
        getNetwork: () => 'Preprod',
        fetch: () => responses.shift(),
        syncMetaYaml: (_idx, yaml) => {
            events.push('sync:' + yaml);
            return true;
        },
        updateRegistryUI: (_idx, data) => events.push('ui:' + data.state),
        startPolling: () => events.push('poll'),
        checkRegistryStatus: () => events.push('check'),
        console,
    };
    vm.createContext(context);
    vm.runInContext(generationSource + '\n' + statusSource, context);

    const older = context.checkRegistryStatus(0, '/flow');
    const newer = context.checkRegistryStatus(0, '/flow');
    second.resolve(registryResponse({
        state: 'NotRegistered',
        updatedYaml: 'new-yaml',
        updatedEtag: '2',
        previousEtag: '1',
    }));
    await newer;
    first.resolve(registryResponse({state: 'RegistrationConfirmed'}));
    await older;

    assert.deepEqual(events, ['sync:new-yaml', 'ui:NotRegistered']);
}

async function testStatusPollsAfterSyncFailure() {
    const events = [];
    const context = {
        exposeName: 'expose',
        getNetwork: () => 'Preprod',
        fetch: async () => registryResponse({
            state: 'RegistrationConfirmed',
            pendingMigration: {registrationId: 'pending'},
            updatedYaml: 'server-yaml',
            updatedEtag: '2',
            previousEtag: '1',
        }),
        syncMetaYaml: () => {
            events.push('sync');
            return false;
        },
        updateRegistryUI: () => events.push('ui'),
        startPolling: () => events.push('poll'),
        console,
    };
    vm.createContext(context);
    vm.runInContext(generationSource + '\n' + statusSource, context);
    await context.checkRegistryStatus(0, '/flow');
    assert.deepEqual(events, ['sync', 'poll']);
}

async function testNotRegisteredStopsPolling() {
    let pollTick;
    const events = [];
    const context = {
        exposeName: 'expose',
        pollIntervals: {},
        pollInFlight: {},
        setInterval: (callback) => {
            pollTick = callback;
            return 7;
        },
        clearInterval: (handle) => events.push('clear:' + handle),
        beginRegistryRequest: () => 1,
        isCurrentRegistryRequest: () => true,
        fetch: async () => registryResponse({
            state: 'NotRegistered',
            updatedYaml: 'cleared-yaml',
            updatedEtag: '2',
            previousEtag: '1',
        }),
        syncMetaYaml: (_idx, yaml) => {
            events.push('sync:' + yaml);
            return true;
        },
        updateRegistryUI: (_idx, data) => events.push('ui:' + data.state),
        updateBadge: () => events.push('badge'),
        showRegError: () => events.push('error'),
        document: {getElementById: () => null},
        console,
    };
    vm.createContext(context);
    vm.runInContext(pollingSource, context);
    context.startPolling(0, '/flow', false);
    await pollTick();

    assert.deepEqual(events, [
        'clear:7',
        'sync:cleared-yaml',
        'ui:NotRegistered',
    ]);
}

async function testStalePollStillReconcilesYaml() {
    let pollTick;
    const events = [];
    const context = {
        exposeName: 'expose',
        pollIntervals: {},
        pollInFlight: {},
        setInterval: (callback) => {
            pollTick = callback;
            return 9;
        },
        clearInterval: () => events.push('clear'),
        beginRegistryRequest: () => 1,
        isCurrentRegistryRequest: () => false,
        fetch: async () => registryResponse({
            state: 'RegistrationConfirmed',
            updatedYaml: 'accepted-yaml',
            updatedEtag: '2',
            previousEtag: '1',
        }),
        syncMetaYaml: (_idx, yaml) => {
            events.push('sync:' + yaml);
            return true;
        },
        updateRegistryUI: () => events.push('ui'),
        updateBadge: () => events.push('badge'),
        showRegError: () => events.push('error'),
        document: {getElementById: () => null},
        console,
    };
    vm.createContext(context);
    vm.runInContext(pollingSource, context);
    context.startPolling(0, '/flow', false);
    await pollTick();
    assert.deepEqual(events, ['sync:accepted-yaml']);
}

async function testCancelMigrationDeduplicatesClicks() {
    const response = deferred();
    const button = {disabled: false};
    const events = [];
    let fetchCount = 0;
    const context = {
        exposeName: 'expose',
        pollIntervals: {},
        confirm: () => true,
        document: {
            getElementById: (id) => ({
                registry_0: {dataset: {flowUrl: '/flow'}},
                cancel_mig_btn_0: button,
            })[id] || null,
        },
        hideRegError: () => {},
        beginRegistryRequest: () => 1,
        isCurrentRegistryRequest: () => true,
        fetch: () => {
            fetchCount++;
            return response.promise;
        },
        showRegError: (_idx, message) => events.push(message),
        startPolling: () => {},
        syncMetaYaml: () => true,
        checkRegistryStatus: () => {},
    };
    vm.createContext(context);
    vm.runInContext(cancelSource, context);
    const first = context.cancelMigration(0);
    const second = context.cancelMigration(0);
    assert.equal(fetchCount, 1);
    assert.equal(button.disabled, true);
    response.resolve(registryResponse({detail: 'already cancelled'}, false));
    await Promise.all([first, second]);
    assert.equal(button.disabled, false);
    assert.deepEqual(events, ['already cancelled']);
}

async function testCancelNoticeSurvivesStatusRefresh() {
    const button = {disabled: false, style: {}};
    const info = {style: {}, textContent: ''};
    const events = [];
    const context = {
        exposeName: 'expose',
        pollIntervals: {},
        confirm: () => true,
        document: {
            getElementById: (id) => ({
                registry_0: {dataset: {flowUrl: '/flow'}},
                cancel_mig_btn_0: button,
                reg_info_0: info,
                etag: {value: '1'},
            })[id] || null,
            querySelector: () => null,
        },
        hideRegError: () => {},
        beginRegistryRequest: () => 1,
        isCurrentRegistryRequest: () => true,
        fetch: async () => registryResponse({
            updatedYaml: 'cancelled-yaml',
            updatedEtag: '2',
            previousEtag: '1',
            notice: 'Check the orphaned mint manually.',
        }),
        showRegError: () => {},
        blockRegistrySync: () => {},
        startPolling: () => {},
        syncMetaYaml: () => true,
        checkRegistryStatus: async () => {
            events.push('check');
            info.textContent = 'stale status';
        },
    };
    vm.createContext(context);
    vm.runInContext(cancelSource, context);
    await context.cancelMigration(0);
    assert.deepEqual(events, ['check']);
    assert.equal(info.textContent, 'Check the orphaned mint manually.');
    assert.equal(button.style.display, 'none');
    assert.equal(button.disabled, false);
}

async function runDeregister(response, syncResult = true) {
    const button = {style: {}, disabled: false, innerHTML: ''};
    const info = {style: {}, textContent: ''};
    const events = [];
    const context = {
        exposeName: 'expose',
        confirm: () => true,
        document: {
            getElementById: (id) => ({
                registry_0: {dataset: {flowUrl: '/flow'}},
                dereg_btn_0: button,
                reg_info_0: info,
            })[id] || null,
        },
        beginRegistryRequest: () => 1,
        isCurrentRegistryRequest: () => true,
        fetch: async () => response,
        syncMetaYaml: (_idx, yaml) => {
            events.push('sync:' + yaml);
            return syncResult;
        },
        showRegError: (_idx, message) => events.push('error:' + message),
        blockRegistrySync: (_idx, message) => events.push('error:' + message),
        updateRegistryUI: (_idx, data) => {
            events.push('ui:' + data.agentIdentifier);
        },
        startPolling: () => events.push('poll'),
        checkRegistryStatus: () => events.push('check'),
    };
    vm.createContext(context);
    vm.runInContext(deregisterSource, context);
    await context.deregisterAgent(0);
    return {button, events};
}

async function testDeregisterResponses() {
    const success = await runDeregister(registryResponse({
        state: 'DeregistrationRequested',
        agentIdentifier: 'active-agent',
        updatedYaml: 'requested-yaml',
        updatedEtag: '2',
        previousEtag: '1',
    }));
    assert.deepEqual(success.events, [
        'sync:requested-yaml',
        'ui:active-agent',
        'poll',
    ]);

    const unsynced = await runDeregister(registryResponse({
        state: 'DeregistrationRequested',
        agentIdentifier: 'active-agent',
        updatedYaml: 'requested-yaml',
    }), false);
    assert.deepEqual(unsynced.events, ['sync:requested-yaml', 'poll']);

    const confirmed = await runDeregister(registryResponse({
        state: 'DeregistrationConfirmed',
        updatedYaml: 'cleared-yaml',
    }));
    assert.deepEqual(confirmed.events, [
        'sync:cleared-yaml',
        'ui:undefined',
    ]);

    const failed = await runDeregister(registryResponse({
        detail: 'request rejected',
        extra: {
            updatedYaml: 'failed-yaml',
            updatedEtag: '3',
            previousEtag: '1',
        },
    }, false));
    assert.deepEqual(failed.events, [
        'sync:failed-yaml',
        'error:request rejected',
        'poll',
    ]);
    assert.equal(failed.button.disabled, false);
    assert.match(failed.button.innerHTML, /Deregister/);

    const networkError = await runDeregister(
        Promise.reject(new Error('connection lost')),
    );
    assert.deepEqual(networkError.events, [
        'error:Network error: connection lost. Reload the page before another registry action.',
        'check',
    ]);
}

for (const functionName of [
    'submitRegistration',
    'submitMigration',
    'deregisterPrevious',
    'deregisterAgent',
]) {
    const functionStart = dialogSource.indexOf(
        'async function ' + functionName + '(',
    );
    const nextFunction = dialogSource.indexOf(
        '\nasync function ', functionStart + 1,
    );
    const functionBody = dialogSource.slice(
        functionStart,
        nextFunction === -1 ? dialogSource.length : nextFunction,
    );
    assert.match(functionBody, /catch \(e\)[\s\S]*checkRegistryStatus\(idx, flowUrl\)/);
    const catchStart = functionBody.indexOf('catch (e)');
    const blockStart = functionBody.indexOf('blockRegistrySync(', catchStart);
    const generationGuard = functionBody.indexOf(
        'if (!isCurrentRegistryRequest', catchStart,
    );
    assert.ok(blockStart > catchStart && blockStart < generationGuard);
}

for (const [functionName, pollingCall] of [
    ['submitRegistration', 'startPolling(idx, flowUrl)'],
    ['submitMigration', 'startPolling(idx, flowUrl, true)'],
]) {
    const functionStart = dialogSource.indexOf(
        'async function ' + functionName + '(',
    );
    const nextFunction = dialogSource.indexOf(
        '\nasync function ', functionStart + 1,
    );
    const functionBody = dialogSource.slice(functionStart, nextFunction);
    const successSync = functionBody.indexOf('var synced = syncMetaYaml(');
    const staleGuard = functionBody.indexOf(
        'if (!isCurrentRegistryRequest', successSync,
    );
    assert.ok(successSync !== -1 && successSync < staleGuard);
    assert.ok(functionBody.indexOf(pollingCall, staleGuard) > staleGuard);
}

assert.match(
    dialogSource.slice(
        dialogSource.indexOf('async function deregisterPrevious('),
        dialogSource.indexOf('async function deregisterAgent('),
    ),
    /data\.state !== 'DeregistrationConfirmed'[\s\S]*startPolling\(idx, flowUrl, true\)/,
);

(async () => {
    await testRegistryResponseOrdering();
    await testStatusPollsAfterSyncFailure();
    await testNotRegisteredStopsPolling();
    await testStalePollStillReconcilesYaml();
    await testCancelMigrationDeduplicatesClicks();
    await testCancelNoticeSurvivesStatusRefresh();
    await testDeregisterResponses();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
