// Drive the real openMigrateDialog against the real loadWallets, with a
// stub fetch standing in for the wallets endpoint.
//
// The dialog used to say "No Web3CardanoV2 selling wallet on this network"
// for every empty list, including lists that failed to load, and it read
// the copy of the list taken at page load. Each case below pins one of the
// answers a node can give to the sentence the operator should see.
//
// Run it with: node tests/test_migrate_dialog.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const walletsSource = fs.readFileSync(
    'kodosumi/service/admin/templates/expose/_registry_wallets_script.html',
    'utf8');
const dialogSource = fs.readFileSync(
    'kodosumi/service/admin/templates/expose/_registry_dialog_script.html',
    'utf8');

const start = dialogSource.indexOf('let migrateDialogGeneration = 0;');
assert.notEqual(start, -1);
const end = dialogSource.indexOf('\nasync function submitMigration(', start);
assert.ok(end > start);
const openSource = dialogSource.slice(start, end);

function makeElement(extra) {
    return Object.assign({
        value: '',
        textContent: '',
        innerHTML: '',
        checked: false,
        disabled: false,
        style: {},
        children: [],
        appendChild(child) { this.children.push(child); },
        showModal() { this.shown = true; this.open = true; },
        close() { this.open = false; },
    }, extra || {});
}

async function open(responder) {
    const elements = {
        mig_current_agent: makeElement(),
        mig_pricing: makeElement(),
        mig_wallet: makeElement(),
        mig_submit: makeElement(),
        mig_error: makeElement(),
        mig_deregister: makeElement(),
        mig_api_base_url: makeElement(),
        'migrate-dialog': makeElement(),
    };
    const infoLines = [];
    let fetchCount = 0;

    const context = {
        console,
        exposeName: 'meme-copy',
        activeDialogIdx: null,
        hideRegError() {},
        jsyaml: {load: () => ({agentIdentifier: 'ad6424e3…1855',
                               agentPricing: [{pricingType: 'Fixed',
                                               fixedPricing: [{amount: '900000',
                                                               unit: 'usdm'}]}]})},
        fetch: async () => { fetchCount += 1; return responder(); },
        document: {
            querySelector: () => ({value: 'display: Meme Copy'}),
            querySelectorAll: (selector) => {
                if (selector === '.registry-section') {
                    return [{id: 'registry_0'}];
                }
                return [];
            },
            getElementById: (id) => {
                if (id === 'reg_info_0') {
                    const info = makeElement();
                    infoLines.push(info);
                    return info;
                }
                return elements[id] || null;
            },
            createElement: () => makeElement(),
        },
    };
    vm.createContext(context);
    vm.runInContext(walletsSource + '\n' + openSource, context);
    await context.openMigrateDialog(0);
    return {elements, context, fetchCount, infoLines};
}

function jsonResponse(payload) {
    return {ok: true, status: 200, json: async () => payload};
}

(async () => {
    // 1. The node answered, and it said why the list is empty.
    let run = await open(() => jsonResponse({
        wallets: [],
        error: "The Masumi API token sees 1 payment source(s), on Preprod, " +
               "and none on 'Mainnet'. Either the network limit of the token " +
               "excludes 'Mainnet', or KODO_MASUMI points at a different node.",
    }));
    console.log('\n1. node reports a reason');
    console.log('   dialog :', run.elements.mig_error.textContent);
    console.log('   submit disabled:', run.elements.mig_submit.disabled);
    assert.match(run.elements.mig_error.textContent, /Preprod/);
    assert.equal(run.elements.mig_submit.disabled, true);

    // 2. Wallets loaded, and every one of them is V1.
    run = await open(() => jsonResponse({wallets: [
        {walletVkey: 'vkey-v1-aaaaaa', walletAddress: 'addr1',
         note: 'seller', paymentSourceType: 'Web3CardanoV1'},
    ]}));
    console.log('\n2. wallets load, all V1');
    console.log('   dialog :', run.elements.mig_error.textContent);
    console.log('   submit disabled:', run.elements.mig_submit.disabled);
    assert.equal(
        run.elements.mig_error.textContent,
        'No Web3CardanoV2 selling wallet on this network. ' +
        'Add one in the Masumi payment service first.');
    assert.equal(run.elements.mig_submit.disabled, true);

    // 3. A V2 wallet exists. It must be offered, and the list must be read
    //    while the dialog opens, not taken from the page load.
    run = await open(() => jsonResponse({wallets: [
        {walletVkey: 'vkey-v2-bbbbbb', walletAddress: 'addr2',
         note: 'v2 seller', paymentSourceType: 'Web3CardanoV2'},
        {walletVkey: 'vkey-v1-aaaaaa', walletAddress: 'addr1',
         note: 'seller', paymentSourceType: 'Web3CardanoV1'},
    ]}));
    console.log('\n3. a V2 wallet exists');
    console.log('   fetches during open:', run.fetchCount);
    console.log('   options:',
                run.elements.mig_wallet.children.map((o) => o.textContent));
    console.log('   submit disabled:', run.elements.mig_submit.disabled);
    assert.equal(run.fetchCount, 1);
    assert.equal(run.elements.mig_wallet.children.length, 1);
    assert.equal(run.elements.mig_wallet.children[0].textContent,
                 'v2 seller [V2]');
    assert.equal(run.elements.mig_submit.disabled, false);
    assert.equal(run.elements.mig_error.style.display, 'none');

    // 4. The endpoint itself failed. The dialog must not claim a missing
    //    V2 wallet on the strength of a list that never arrived.
    run = await open(() => ({ok: false, status: 502, json: async () => ({})}));
    console.log('\n4. wallets endpoint fails');
    console.log('   dialog :', run.elements.mig_error.textContent);
    assert.match(run.elements.mig_error.textContent, /HTTP 502/);
    assert.doesNotMatch(run.elements.mig_error.textContent, /Add one in the/);

    // 5. The url override starts empty on every open.
    console.log('\n5. url override default:',
                JSON.stringify(run.elements.mig_api_base_url.value));
    assert.equal(run.elements.mig_api_base_url.value, '');

    // 6. Reopen the same flow while the first load is still in flight.
    //    The late one must not append a second copy of the options.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const v2 = {wallets: [{walletVkey: 'vkey-v2-bbbbbb',
                               walletAddress: 'addr2', note: 'v2 seller',
                               paymentSourceType: 'Web3CardanoV2'}]};
        let release;
        const gate = new Promise((r) => { release = r; });
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                call += 1;
                if (call === 1) { await gate; }
                return jsonResponse(v2);
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => id === 'reg_info_0'
                    ? makeElement() : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        const first = context.openMigrateDialog(0);
        context.activeDialogIdx = null;          // Escape closes the dialog
        const second = context.openMigrateDialog(0);
        await second;
        release();
        await first;
        console.log('\n6. reopened while the first load was in flight');
        console.log('   options:',
                    elements.mig_wallet.children.map((o) => o.textContent));
        assert.equal(elements.mig_wallet.children.length, 1);
    }

    // 7. A list that loaded but is short one payment source. The dialog
    //    works, so the caution must not be painted as a refusal.
    run = await open(() => jsonResponse({
        wallets: [{walletVkey: 'vkey-v2-bbbbbb', walletAddress: 'addr2',
                   note: 'v2 seller', paymentSourceType: 'Web3CardanoV2'}],
        warning: 'This wallet list may be incomplete: GET /wallet for ' +
                 'payment source src-v1 answered HTTP 503',
    }));
    console.log('\n7. partial list, V2 wallet present');
    console.log('   dialog :', run.elements.mig_error.textContent);
    console.log('   colour :', run.elements.mig_error.style.color);
    console.log('   submit disabled:', run.elements.mig_submit.disabled);
    assert.match(run.elements.mig_error.textContent, /may be incomplete/);
    assert.equal(run.elements.mig_error.style.color,
                 'var(--on-surface-variant)');
    assert.equal(run.elements.mig_submit.disabled, false);

    // 8. The operator closes the dialog while the quiet load is running and
    //    the load then fails. The reason must reach the page instead of
    //    disappearing with the dialog.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const info = makeElement();
        let release;
        const gate = new Promise((r) => { release = r; });
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                await gate;
                return {ok: false, status: 500, json: async () => ({})};
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => id === 'reg_info_0'
                    ? info : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        const pending = context.openMigrateDialog(0);
        context.activeDialogIdx = null;      // Escape closes the dialog
        release();
        await pending;
        console.log('\n8. dialog closed while the quiet load failed');
        console.log('   page   :', info.textContent);
        assert.match(info.textContent, /HTTP 500/);
    }

    // 9. Every write into the migrate error element has to set its colour
    //    too. The element carries a muted caution and a red refusal in the
    //    same dialog, so a write that leaves the colour alone inherits the
    //    previous meaning: a refused submit rendered as a mild note.
    {
        const lines = dialogSource.split('\n');
        const offenders = [];
        lines.forEach((line, i) => {
            if (!/errEl\.textContent\s*=/.test(line)) return;
            // The colour may be set on either side of the write, so read a
            // window around it rather than only what came before.
            const window = lines.slice(Math.max(0, i - 3), i + 4).join('\n');
            if (!/errEl\.style\.color\s*=/.test(window)) {
                offenders.push(i + 1);
            }
        });
        console.log('\n9. error element writes that set a colour');
        console.log('   writes without one, by line:', offenders);
        assert.deepEqual(offenders, []);
    }

    // 10. The operator presses Escape and then opens the register dialog
    //     on the same section. activeDialogIdx belongs to both dialogs, so
    //     it points at this section again while the migrate dialog is
    //     shut. The reason must still reach the page, not the hidden
    //     migrate error line.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const info = makeElement();
        let release;
        const gate = new Promise((r) => { release = r; });
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                await gate;
                return {ok: false, status: 503, json: async () => ({})};
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => id === 'reg_info_0'
                    ? info : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        const pending = context.openMigrateDialog(0);
        elements['migrate-dialog'].close();   // Escape shuts the dialog
        context.activeDialogIdx = 0;          // the register dialog claims it
        release();
        await pending;
        console.log('\n10. register dialog opened on the same section');
        console.log('   page   :', info.textContent);
        console.log('   hidden :', elements.mig_error.textContent);
        assert.match(info.textContent, /HTTP 503/);
        assert.equal(elements.mig_error.textContent, '');
    }

    console.log('\nall ten dialog cases behaved as expected');
})();
