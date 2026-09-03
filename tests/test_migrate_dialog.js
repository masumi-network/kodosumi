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
// Take openMigrateDialog and submitMigration together: the dialog
// writes the api_base_url field and the submit reads it, and a test
// that loads only the first half cannot see the field leave the page.
const end = dialogSource.indexOf('\nasync function deregisterPrevious(', start);
assert.ok(end > start);
const openSource = dialogSource.slice(start, end);

// The real generation helpers, not stubs. Stubs that ignore their
// arguments cannot see submitMigration passing the wrong idx or losing
// the generation, and either mistake silently swallows every refusal.
const registrySource = fs.readFileSync(
    'kodosumi/service/admin/templates/expose/_registry_script.html', 'utf8');
const helpersStart = registrySource.indexOf(
    'const registryRequestGenerations = {};');
assert.notEqual(helpersStart, -1);
const helpersEnd = registrySource.indexOf(
    '\nfunction getNetworkEl(', helpersStart);
assert.ok(helpersEnd > helpersStart);
const helpersSource = registrySource.slice(helpersStart, helpersEnd);

function makeElement(extra) {
    const el = Object.assign({
        value: '',
        textContent: '',
        checked: false,
        disabled: false,
        style: {},
        children: [],
        appendChild(child) { this.children.push(child); },
        showModal() { this.shown = true; this.open = true; },
        close() { this.open = false; },
    }, extra || {});
    // Writing innerHTML replaces the children. Modelling it as a plain
    // field made every missing reset invisible: a select that was never
    // cleared before it was refilled looked the same as one that was.
    let html = '';
    Object.defineProperty(el, 'innerHTML', {
        get() { return html; },
        set(value) { html = value; this.children = []; },
        enumerable: true,
        configurable: true,
    });
    return el;
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

    // 5. The url override starts empty on every open. A fresh element is
    //    empty anyway, so type into it and reopen: only a real reset can
    //    clear it. A url left over from an abandoned dialog would be
    //    minted on chain by the next migration.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => jsonResponse({wallets: []}),
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
        await context.openMigrateDialog(0);
        elements.mig_api_base_url.value = 'https://stale.example/sumi/old';
        elements.mig_deregister.checked = true;
        context.activeDialogIdx = null;
        await context.openMigrateDialog(0);
        console.log('\n5. url override after a reopen:',
                    JSON.stringify(elements.mig_api_base_url.value));
        assert.equal(elements.mig_api_base_url.value, '');
        assert.equal(elements.mig_deregister.checked, false);
    }

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
        // Escape really closes the dialog: oncancel nulls the index and
        // the element reports itself shut. A test that only nulls the
        // index leaves .open true and stops testing the real thing.
        context.activeDialogIdx = null;
        elements['migrate-dialog'].close();
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
        elements['migrate-dialog'].close();
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

    // 11. The url the operator typed has to leave the browser. Nothing
    //     else in the suite loads submitMigration, so the field could be
    //     dropped from the request body and every other case would pass.
    //     The refusal that follows also has to be painted as a refusal.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement({value: 'vkey-v2-bbbbbb'}),
            mig_submit: makeElement(),
            mig_error: makeElement({style: {color: 'var(--on-surface-variant)'}}),
            mig_deregister: makeElement({checked: true}),
            mig_api_base_url: makeElement(
                {value: '  https://v2.example/sumi/flow  '}),
            'migrate-dialog': makeElement(),
            registry_0: makeElement({dataset: {flowUrl: '/flow/x'}}),
            etag: makeElement({value: 'etag-1'}),
        };
        let sent = null;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: 0,
            hideRegError() {},
            showRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async (url, init) => {
                sent = JSON.parse(init.body);
                return {ok: false, status: 422,
                        json: async () => ({detail: 'api_base_url is wrong'})};
            },
            document: {
                querySelector: () => ({value: 'display: x'}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => elements[id] || null,
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(
            helpersSource + '\n' + walletsSource + '\n' + openSource,
            context);
        await context.submitMigration();
        console.log('\n11. submit sends the url the operator typed');
        console.log('   api_base_url :', JSON.stringify(sent.api_base_url));
        console.log('   wallet_vkey  :', JSON.stringify(sent.wallet_vkey));
        console.log('   refusal text :', elements.mig_error.textContent);
        console.log('   refusal colour:',
                    JSON.stringify(elements.mig_error.style.color));
        assert.equal(sent.api_base_url, 'https://v2.example/sumi/flow');
        assert.equal(sent.wallet_vkey, 'vkey-v2-bbbbbb');
        assert.equal(sent.deregister_previous, true);
        assert.equal(elements.mig_error.textContent, 'api_base_url is wrong');
        // A refusal must not inherit the muted colour of a caution.
        assert.equal(elements.mig_error.style.color, '');
    }

    // 12. A failed reload must drop the list the previous load produced.
    //     Without that, the dialog offers a wallet the node has just
    //     failed to confirm and lets the operator mint against it.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                call += 1;
                if (call === 1) {
                    return jsonResponse({wallets: [
                        {walletVkey: 'vkey-v2-old', walletAddress: 'addr2',
                         note: 'v2 seller',
                         paymentSourceType: 'Web3CardanoV2'}]});
                }
                return {ok: false, status: 502, json: async () => ({})};
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}]
                    : (s === 'select.wallet-select' ? [makeElement()] : []),
                getElementById: (id) => id === 'reg_info_0'
                    ? makeElement() : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        await context.loadWallets();                 // page load succeeds
        // loadedWallets is a `let`, so it is a lexical binding of the
        // script and never a property of the context object.
        assert.equal(vm.runInContext('loadedWallets.length', context), 1);
        await context.openMigrateDialog(0);          // reload then fails
        console.log('\n12. failed reload drops the earlier list');
        console.log('   options :',
                    elements.mig_wallet.children.map((o) => o.textContent));
        console.log('   dialog  :', elements.mig_error.textContent);
        console.log('   submit disabled:', elements.mig_submit.disabled);
        assert.deepEqual(elements.mig_wallet.children, []);
        assert.equal(elements.mig_submit.disabled, true);
        assert.match(elements.mig_error.textContent, /HTTP 502/);
    }

    // 13. Two loads overlap and the abandoned one lands first. Its answer
    //     must not refill the list the live load just cleared, or the
    //     dialog offers a wallet the node no longer reports, with Submit
    //     enabled, beside the words "could not be loaded".
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const pageInfo = makeElement();
        let releaseFirst, releaseSecond;
        const first = new Promise((r) => { releaseFirst = r; });
        const second = new Promise((r) => { releaseSecond = r; });
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                call += 1;
                if (call === 1) {
                    await first;
                    return jsonResponse({wallets: [
                        {walletVkey: 'vkey-v2-STALE', walletAddress: 'addr',
                         note: 'wallet from the abandoned load',
                         paymentSourceType: 'Web3CardanoV2'}]});
                }
                await second;
                return {ok: false, status: 502, json: async () => ({})};
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => id === 'reg_info_0'
                    ? pageInfo : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        const opened = context.openMigrateDialog(0);
        // The dialog is on screen for the whole load, so Migrate has to be
        // disabled from the moment it opens, not only once a list arrives.
        assert.equal(elements.mig_submit.disabled, true);
        elements['migrate-dialog'].close();     // Escape
        context.activeDialogIdx = null;
        const reopened = context.openMigrateDialog(0);
        releaseFirst();                          // the abandoned load lands
        await new Promise((r) => setTimeout(r, 0));
        releaseSecond();                         // then the live one fails
        await Promise.all([opened, reopened]);
        console.log('\n13. abandoned load lands before the live one');
        console.log('   options :',
                    elements.mig_wallet.children.map((o) => o.textContent));
        console.log('   dialog  :', elements.mig_error.textContent);
        console.log('   submit disabled:', elements.mig_submit.disabled);
        assert.deepEqual(elements.mig_wallet.children, []);
        assert.equal(elements.mig_submit.disabled, true);
        assert.match(elements.mig_error.textContent, /HTTP 502/);
        // The dialog reloads quietly, so the failure belongs in the dialog
        // and nowhere else. Painting the sections here would leave red text
        // on flows the operator never opened.
        console.log('   page   :', JSON.stringify(pageInfo.textContent));
        assert.equal(pageInfo.textContent, '');
    }

    // 14. The mirror image: an abandoned FAILURE must not overwrite the
    //     reason of a load that succeeded after it.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        let releaseFirst;
        const first = new Promise((r) => { releaseFirst = r; });
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                call += 1;
                if (call === 1) {
                    await first;
                    return {ok: false, status: 500, json: async () => ({})};
                }
                return jsonResponse({wallets: [
                    {walletVkey: 'vkey-v2-live', walletAddress: 'addr',
                     note: 'v2 seller',
                     paymentSourceType: 'Web3CardanoV2'}]});
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
        const opened = context.openMigrateDialog(0);
        elements['migrate-dialog'].close();
        context.activeDialogIdx = null;
        const reopened = context.openMigrateDialog(0);
        await reopened;
        releaseFirst();
        await opened;
        console.log('\n14. abandoned failure lands after a good load');
        console.log('   options :',
                    elements.mig_wallet.children.map((o) => o.textContent));
        console.log('   dialog  :',
                    JSON.stringify(elements.mig_error.textContent));
        assert.equal(elements.mig_wallet.children.length, 1);
        assert.equal(elements.mig_submit.disabled, false);
        assert.equal(elements.mig_error.textContent, '');
        // The three assertions above are decided by the dialog's own
        // generation counter, which predates this fix. The shared state
        // is what the retirement protects, so read it directly.
        assert.equal(vm.runInContext('walletLoadError', context), '');
        assert.equal(vm.runInContext('loadedWallets.length', context), 1);
    }

    // 15. A failed load paints every registry section, including flows the
    //     operator never touched. Nothing else takes that message down, so
    //     a later good load has to.
    {
        const info = {0: makeElement(), 1: makeElement()};
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            fetch: async () => {
                call += 1;
                return call === 1
                    ? {ok: false, status: 500, json: async () => ({})}
                    : jsonResponse({wallets: [
                        {walletVkey: 'vkey-v2', walletAddress: 'addr',
                         note: 'v2 seller',
                         paymentSourceType: 'Web3CardanoV2'}]});
            },
            document: {
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}, {id: 'registry_1'}]
                    : (s === 'select.wallet-select' ? [makeElement()] : []),
                getElementById: (id) => id === 'reg_info_0' ? info[0]
                    : (id === 'reg_info_1' ? info[1] : null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource, context);
        await context.loadWallets();
        console.log('\n15. a good load takes down what a bad one wrote');
        console.log('   after the failure, section 1:',
                    JSON.stringify(info[1].textContent));
        assert.match(info[0].textContent, /HTTP 500/);
        assert.match(info[1].textContent, /HTTP 500/);
        // updateRegistryUI owns these elements too. A status line it wrote
        // over the wallet message must survive the clear.
        info[1].textContent = 'Waiting for on-chain confirmation...';
        await context.loadWallets();
        console.log('   after the good load, section 0:',
                    JSON.stringify(info[0].textContent),
                    'display=', JSON.stringify(info[0].style.display));
        console.log('   a status line elsewhere  :',
                    JSON.stringify(info[1].textContent));
        assert.equal(info[0].textContent, '');
        assert.equal(info[0].style.display, 'none');
        assert.equal(info[1].textContent,
                     'Waiting for on-chain confirmation...');
    }

    // 16. The network drops mid-submit. Nothing else runs this branch, so
    //     without it the refusal colour there rests on a text scan that a
    //     reformat could satisfy while the bug stayed.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement({value: 'vkey-v2'}),
            mig_submit: makeElement(),
            mig_error: makeElement(
                {style: {color: 'var(--on-surface-variant)'}}),
            mig_deregister: makeElement(), mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
            registry_0: makeElement({dataset: {flowUrl: '/flow/x'}}),
            etag: makeElement({value: 'etag-1'}),
        };
        let blocked = null;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: 0,
            hideRegError() {}, showRegError() {},
            blockRegistrySync: (idx, message) => { blocked = message; },
            checkRegistryStatus: async () => {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => { throw new Error('Failed to fetch'); },
            document: {
                querySelector: () => ({value: 'display: x'}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => elements[id] || null,
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(
            helpersSource + '\n' + walletsSource + '\n' + openSource,
            context);
        await context.submitMigration();
        console.log('\n16. the network drops mid-submit');
        console.log('   dialog :', elements.mig_error.textContent);
        console.log('   colour :',
                    JSON.stringify(elements.mig_error.style.color));
        console.log('   blocked:', blocked);
        assert.equal(elements.mig_error.textContent,
                     'Network error: Failed to fetch');
        assert.equal(elements.mig_error.style.color, '');
        assert.match(blocked, /Reload the page/);
        // The finally block hands the button back once the dialog is free.
        assert.equal(elements.mig_submit.disabled, false);
    }

    // 17. Reopening the dialog must not stack a second copy of the same
    //     wallet on the select. Both renders really happen here, so only
    //     the clear before the refill can keep the list at one.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => jsonResponse({wallets: [
                {walletVkey: 'vkey-v2', walletAddress: 'addr',
                 note: 'v2 seller', paymentSourceType: 'Web3CardanoV2'}]}),
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
        await context.openMigrateDialog(0);
        elements['migrate-dialog'].close();
        context.activeDialogIdx = null;
        await context.openMigrateDialog(0);
        console.log('\n17. reopening does not stack the options');
        console.log('   options :',
                    elements.mig_wallet.children.map((o) => o.textContent));
        assert.equal(elements.mig_wallet.children.length, 1);
    }

    // 18. The same, for the page's own wallet selects, which loadWallets
    //     refills on every load.
    {
        const select = makeElement();
        const context = {
            console, exposeName: 'meme-copy', hideRegError() {},
            fetch: async () => jsonResponse({wallets: [
                {walletVkey: 'vkey-v2', walletAddress: 'addr',
                 note: 'v2 seller', paymentSourceType: 'Web3CardanoV2'}]}),
            document: {
                querySelectorAll: (s) => s === 'select.wallet-select'
                    ? [select] : [],
                getElementById: () => null,
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource, context);
        await context.loadWallets();
        await context.loadWallets();
        console.log('\n18. a second load refills, it does not append');
        console.log('   options :',
                    select.children.map((o) => o.textContent));
        assert.equal(select.children.length, 1);
    }

    // 19. A submit that is still in flight when the operator reopens the
    //     dialog re-enables Migrate from its finally block. The empty-list
    //     branch has to take the button back, or Migrate sits enabled
    //     beside the words "could not be listed" with nothing selected.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement({value: 'vkey-v2'}),
            mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
            registry_0: makeElement({dataset: {flowUrl: '/flow/x'}}),
            etag: makeElement({value: 'etag-1'}),
        };
        elements['migrate-dialog'].showModal();
        let releasePost, releaseWallets;
        const post = new Promise((r) => { releasePost = r; });
        const wallets = new Promise((r) => { releaseWallets = r; });
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: 0,
            hideRegError() {}, showRegError() {},
            blockRegistrySync() {}, checkRegistryStatus: async () => {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async (url) => {
                if (String(url).indexOf('/registry/migrate') !== -1) {
                    await post;
                    return {ok: false, status: 422,
                            json: async () => ({detail: 'nope'})};
                }
                await wallets;
                return {ok: false, status: 502, json: async () => ({})};
            },
            document: {
                querySelector: () => ({value: 'display: x'}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}] : [],
                getElementById: (id) => id === 'reg_info_0'
                    ? makeElement() : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(
            helpersSource + '\n' + walletsSource + '\n' + openSource,
            context);
        const submitting = context.submitMigration();
        context.activeDialogIdx = null;              // Escape
        elements['migrate-dialog'].close();
        const reopened = context.openMigrateDialog(0);
        releasePost();                                // finally re-enables
        await submitting;
        releaseWallets();                             // then the load fails
        await reopened;
        console.log('\n19. a late submit re-enables Migrate mid-load');
        console.log('   dialog :', elements.mig_error.textContent);
        console.log('   submit disabled:', elements.mig_submit.disabled);
        assert.match(elements.mig_error.textContent, /could be listed/);
        assert.deepEqual(elements.mig_wallet.children, []);
        assert.equal(elements.mig_submit.disabled, true);
    }

    // 20. A retired load whose response is fine and whose json() is what
    //     straddles the newer load must still write nothing.
    {
        let releaseJson;
        const jsonGate = new Promise((r) => { releaseJson = r; });
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', hideRegError() {},
            fetch: async () => {
                call += 1;
                if (call === 1) {
                    return {ok: true, status: 200, json: async () => {
                        await jsonGate;
                        return {wallets: [
                            {walletVkey: 'vkey-STALE', walletAddress: 'a',
                             note: 'stale',
                             paymentSourceType: 'Web3CardanoV2'}]};
                    }};
                }
                return jsonResponse({wallets: []});
            },
            document: {
                querySelectorAll: () => [],
                getElementById: () => null,
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource, context);
        const first = context.loadWallets();
        await new Promise((r) => setTimeout(r, 0));
        const second = context.loadWallets();
        await second;
        releaseJson();
        await first;
        console.log('\n20. retired between the response and its json');
        console.log('   loadedWallets:',
                    vm.runInContext('loadedWallets.length', context));
        console.log('   walletLoadError:',
                    JSON.stringify(vm.runInContext('walletLoadError',
                                                   context)));
        assert.equal(vm.runInContext('loadedWallets.length', context), 0);
        assert.match(vm.runInContext('walletLoadError', context),
                     /No selling wallets found/);
    }

    // 21. A retired load that throws must not report its failure over the
    //     live load's good list.
    {
        let releaseError;
        const errorGate = new Promise((r) => { releaseError = r; });
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', hideRegError() {},
            fetch: async () => {
                call += 1;
                if (call === 1) {
                    await errorGate;
                    throw new Error('boom');
                }
                return jsonResponse({wallets: [
                    {walletVkey: 'vkey-v2', walletAddress: 'addr',
                     note: 'v2 seller',
                     paymentSourceType: 'Web3CardanoV2'}]});
            },
            document: {
                querySelectorAll: (s) => s === 'select.wallet-select'
                    ? [makeElement()] : [],
                getElementById: () => null,
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource, context);
        const first = context.loadWallets();
        const second = context.loadWallets();
        await second;
        releaseError();
        await first;
        console.log('\n21. a retired load throws after a good one landed');
        console.log('   loadedWallets:',
                    vm.runInContext('loadedWallets.length', context));
        console.log('   walletLoadError:',
                    JSON.stringify(vm.runInContext('walletLoadError',
                                                   context)));
        assert.equal(vm.runInContext('loadedWallets.length', context), 1);
        assert.equal(vm.runInContext('walletLoadError', context), '');
    }

    // 22. A reason from an earlier load must not survive into a later
    //     one that worked. Nothing else clears walletLoadError, so a
    //     stale sentence would sit in the error colour beside a wallet
    //     the operator can actually migrate to.
    {
        const elements = {
            mig_current_agent: makeElement(), mig_pricing: makeElement(),
            mig_wallet: makeElement(), mig_submit: makeElement(),
            mig_error: makeElement(), mig_deregister: makeElement(),
            mig_api_base_url: makeElement(),
            'migrate-dialog': makeElement(),
        };
        let call = 0;
        const context = {
            console, exposeName: 'meme-copy', activeDialogIdx: null,
            hideRegError() {},
            jsyaml: {load: () => ({agentIdentifier: 'a'})},
            fetch: async () => {
                call += 1;
                return call === 1
                    ? {ok: false, status: 503, json: async () => ({})}
                    : jsonResponse({wallets: [
                        {walletVkey: 'vkey-v2', walletAddress: 'addr',
                         note: 'v2 seller',
                         paymentSourceType: 'Web3CardanoV2'}]});
            },
            document: {
                querySelector: () => ({value: ''}),
                querySelectorAll: (s) => s === '.registry-section'
                    ? [{id: 'registry_0'}]
                    : (s === 'select.wallet-select' ? [makeElement()] : []),
                getElementById: (id) => id === 'reg_info_0'
                    ? makeElement() : (elements[id] || null),
                createElement: () => makeElement(),
            },
        };
        vm.createContext(context);
        vm.runInContext(walletsSource + '\n' + openSource, context);
        await context.loadWallets();                 // fails, sets a reason
        assert.match(vm.runInContext('walletLoadError', context), /503/);
        await context.openMigrateDialog(0);          // then a good reload
        console.log('\n22. an old reason does not survive a good load');
        console.log('   dialog :',
                    JSON.stringify(elements.mig_error.textContent),
                    'display=', JSON.stringify(elements.mig_error.style.display));
        console.log('   options:',
                    elements.mig_wallet.children.map((o) => o.textContent));
        assert.equal(vm.runInContext('walletLoadError', context), '');
        assert.equal(elements.mig_error.textContent, '');
        assert.equal(elements.mig_error.style.display, 'none');
        assert.equal(elements.mig_submit.disabled, false);
    }

    console.log('\nall twenty-two dialog cases behaved as expected');
})();
