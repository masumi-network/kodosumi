let last_active = null;
let follow = {
    "page-event": false, 
    "page-output": true,
    "page-stdio": true
};


function scrollBottom() {
    //elm.scrollIntoView({behavior: 'instant'});
    // console.log("scrollBottom", follow);
    if (follow["page-stdio"]) {
        // console.log("scrollBottom: page-stdio");
        elmStdioArticle.scrollTo(0, elmStdioArticle.scrollHeight);
    }
    if (follow["page-event"]) {
        // console.log("scrollBottom: page-event");
        elmEventArticle.scrollTo(0, elmEventArticle.scrollHeight);
    }
    if (follow["page-output"]) {
        // console.log("scrollBottom: page-output");
        const targetElement = document.getElementById('output-end');
        if (targetElement && elmOutputArticle) {
            const containerRect = elmOutputArticle.getBoundingClientRect();
            const targetRect = targetElement.getBoundingClientRect();
            const offsetRelativeToContainer = targetRect.top - containerRect.top;
            const newScrollTop = elmOutputArticle.scrollTop + offsetRelativeToContainer;
            elmOutputArticle.scrollTop = newScrollTop;
        } else {
             if (!targetElement) console.warn("Element with ID 'output-end' not found for scrolling.");
             // Optional fallback: scroll to bottom if target not found
             elmOutputArticle.scrollTo(0, elmOutputArticle.scrollHeight);
        }
    }
}

function scrollDown() {
    // If a timer is already active, do nothing.
    if (scrollDebounceTimer) {
        return;
    }

    // Schedule scrollBottom to run after 250ms
    scrollDebounceTimer = setTimeout(() => {
        scrollBottom();
        // Reset the timer *after* execution, allowing future calls to schedule again.
        scrollDebounceTimer = null;
    }, scrollDebounceMs);
}

const tabModes = document.querySelectorAll('.tab-mode');
tabModes.forEach(tabMode => {
    tabMode.addEventListener('click', (event) => {
        const target = event.target;
        const ui = target.getAttribute('data-ui').substring(1);
        if (last_active != ui) {
            active = false;
        }
        else {
            active = true;
        }
        if (active) {
            follow[ui] = !follow[ui];
            if (follow[ui]) {
                document.querySelector('#' + ui + '-follow').classList.add('fill');
            }
            else {
                document.querySelector('#' + ui + '-follow').classList.remove('fill');
            }
        }
        if (follow[ui]) {
            scrollBottom();
        }
        last_active = ui;
        // console.log(ui, ": ", active, "last: ", last_active);  
    });
});

// Helper function to disable follow and update UI
function disableFollow(key) {
    if (follow[key]) {
        follow[key] = false;
        const followIcon = document.querySelector('#' + key + '-follow');
        if (followIcon) {
            followIcon.classList.remove('fill');
        }
    }
}

elmOutputArticle.addEventListener('click', () => {
    disableFollow('page-output');
});
elmStdioArticle.addEventListener('click', () => {
    disableFollow('page-stdio');
});
elmEventArticle.addEventListener('click', () => {
    disableFollow('page-event');
});
